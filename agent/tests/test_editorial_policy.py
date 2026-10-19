"""Offline editorial-count, exception, render and email layout regressions."""
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bs4 import BeautifulSoup
import espresso_agent as ea
import espresso_loop as el
import render_html as rh
import render_edition as cli
from send_email import _build_email_safe_html

POLICY = '4-5-exceptional-6'
REASON = ('This newly released security fix closes an actively exploited AI agent flaw; '
          'waiting until tomorrow would leave readers without an urgent deployment warning.')


def state_with(n):
    candidates = [dict(id=i, headline=f'AI launch {i}', url=f'https://source{i}.example/news',
                       source=f'Source {i}', tier=1 if i == 0 else 2, score=90-i,
                       body='Verified source evidence about this AI launch. ' * 10)
                  for i in range(n)]
    state = el.AgentState(today=dt.date(2026,10,19), candidates=candidates, archive_headlines=[])
    for i in range(n):
        el._tool_pick(state, dict(id=i,reason='Relevant verified launch',persona='build'), [])
    return state


def edition_data(n):
    return dict(date='2026-10-19', story_count_policy=POLICY,
                stories=[dict(headline=f'AI launch {i}', original_headline=f'AI launch {i}',
                              blurb='Useful new capability.', why_it_matters='Readers can deploy it.',
                              source_name=f'Source {i}',source_url=f'https://source{i}.example/news',
                              tier=1 if i == 0 else 2,slot='build') for i in range(n)],
                try_this_prompt=dict(title='Review a deployment',prompt='Find the risks.',tool_hint='Use before deploying.'))


class EditorialCountPolicyTests(unittest.TestCase):
    def test_four_and_five_ship_normally(self):
        for n in (4,5):
            with self.subTest(n=n):
                self.assertTrue(el._tool_ship(state_with(n),{})['shipped'])

    def test_six_requires_a_substantive_exception_bound_to_a_selected_story(self):
        for args in ({}, {'sixth_story_id':5,'exception_reason':'The pool is strong'},
                     {'sixth_story_id':99,'exception_reason':REASON},
                     {'sixth_story_id':True,'exception_reason':REASON},
                     {'sixth_story_id':5,'exception_reason':['not a string']}):
            with self.subTest(args=args):
                self.assertFalse(el._tool_ship(state_with(6),args)['shipped'])
        state=state_with(6)
        self.assertTrue(el._tool_ship(state,{'sixth_story_id':5,'exception_reason':REASON})['shipped'])
        self.assertEqual(state.six_story_exception['original_headline'],'AI launch 5')
        self.assertEqual(state.six_story_exception['reason'],REASON)

    def test_seventh_pick_and_oversized_slate_are_rejected(self):
        state=state_with(7)
        self.assertEqual(len(state.picks),6)
        state.picks['injected']=dict(state.candidates[-1])
        self.assertFalse(el._tool_ship(state,{'sixth_story_id':5,'exception_reason':REASON})['shipped'])

    def test_fallback_preserves_five_and_trims_unjustified_six_to_five(self):
        for n,expected_removed in ((5,0),(6,1)):
            state=state_with(n)
            removed=el._trim_picks_to_supported_count(state,[])
            self.assertEqual(len(removed),expected_removed)
            self.assertEqual(len(state.picks),5)
            self.assertTrue(el._validate_ship(state)['ok'])

    def test_slate_edit_invalidates_previous_exception(self):
        state=state_with(6)
        el._tool_ship(state,{'sixth_story_id':5,'exception_reason':REASON})
        state.shipped=False
        el._tool_unpick(state,{'id':0},[])
        el._tool_pick(state,{'id':0,'reason':'Restored story','persona':'build'},[])
        self.assertFalse(el._validate_ship(state)['ok'])

    def test_tools_after_finalization_cannot_add_an_unjustified_sixth(self):
        state=state_with(6)
        el._tool_unpick(state,{'id':5},[])
        self.assertTrue(el._tool_ship(state,{})['shipped'])
        result=el._dispatch_tool('pick',{'id':5,'reason':'Extra story','persona':'build'},state,[])
        self.assertIn('error',result)
        self.assertEqual(len(state.picks),5)

    def test_editor_instructions_allow_five_without_filler(self):
        self.assertNotIn('Never ship 5',el._EDITOR_SYSTEM)
        self.assertIn('4 or 5',el._EDITOR_SYSTEM)
        self.assertIn('exception_reason',el._EDITOR_SYSTEM)

    def test_exception_is_persisted_in_edition_json(self):
        edition=ea.Edition(date='2026-10-19')
        edition.six_story_exception=dict(candidate_id=5,original_headline='AI launch 5',reason=REASON)
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(ea,'EDITIONS_DIR',Path(tmp)), \
             mock.patch.object(ea,'append_archive'):
            path=ea.write_edition(edition)
            saved=json.loads(path.read_text())
        self.assertEqual(saved.get('story_count_policy'),POLICY)
        self.assertEqual(saved.get('six_story_exception'),edition.six_story_exception)


class EditorialRenderPolicyTests(unittest.TestCase):
    def images_only(self,data):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'2026-10-19.json').write_text(json.dumps(data))
            with mock.patch.object(cli,'DATA_EDITIONS_DIR',root), mock.patch.object(cli,'EDITIONS_DIR',root), \
                 mock.patch.object(cli,'load_env_file'), \
                 mock.patch.object(cli.sys,'argv',['render_edition.py','2026-10-19','--images-only','--issue-num','999']), \
                 mock.patch.object(cli,'render_images',return_value={'generated':[],'missing':[]}) as images, \
                 mock.patch.object(cli,'render_edition',return_value={'issue_num':999,'html_path':'offline.html','md_path':'offline.md'}):
                try:
                    cli.main()
                finally:
                    self.last_image_calls=images.call_args_list

    def test_images_only_allocates_story_images_plus_separate_prompt(self):
        for n in (4,5,6):
            data=edition_data(n)
            if n==6:
                data['six_story_exception']=dict(candidate_id=5,original_headline='AI launch 5',reason=REASON)
            self.images_only(data)
            self.assertEqual(len(self.last_image_calls[0].args[0]['image_paths']),n+1)

    def test_images_only_rejects_invalid_slate_before_image_provider_calls(self):
        for n in (3,6,7):
            with self.subTest(n=n), self.assertRaises(ValueError): self.images_only(edition_data(n))
            self.assertEqual(self.last_image_calls,[])

    def render(self,data):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); source=root/'edition.json'; source.write_text(json.dumps(data))
            result=rh.render_edition(source,issue_num=999,editions_dir=root)
            return Path(result['html_path']).read_text(),Path(result['md_path']).read_text(),result

    def test_five_stories_keep_all_content_and_one_separate_prompt_image(self):
        html,md,result=self.render(edition_data(5))
        soup=BeautifulSoup(html,'html.parser')
        self.assertEqual(len(soup.select('.story-cards .card')),5)
        self.assertEqual(len(soup.select('.prompt-card')),1)
        self.assertEqual(len(result['image_paths']),6)
        for i in range(5): self.assertIn(f'AI launch {i}',md)
        self.assertIn('.edition-grid--n5',html)
        safe=BeautifulSoup(_build_email_safe_html(html),'html.parser')
        outer=safe.find('table',attrs={'width':'680'})
        story_rows=[row for row in outer.find_all('tr',recursive=False) if row.find('h2')]
        self.assertEqual([len(row.find_all('h2')) for row in story_rows],[3,2])
        cells=[cell for row in story_rows for cell in row.find_all('td',recursive=False) if cell.find('h2')]
        self.assertEqual({cell['width'] for cell in cells},{'33%'})
        # Narrow three-column cards need enough common height for wrapped copy.
        heights={int(heading.find_parent('table')['height']) for heading in safe.find_all('h2')}
        self.assertEqual(len(heights),1)
        self.assertGreaterEqual(heights.pop(),440)

    def test_new_six_story_edition_requires_persisted_exception(self):
        data=edition_data(6)
        with self.assertRaisesRegex(ValueError,'exception'): self.render(data)
        data['six_story_exception']=dict(candidate_id=5,original_headline='AI launch 5',reason=REASON)
        html,_,result=self.render(data)
        self.assertEqual(len(BeautifulSoup(html,'html.parser').select('.story-cards .card')),6)
        self.assertEqual(len(result['image_paths']),7)
        data['six_story_exception']['original_headline']='Not in this edition'
        with self.assertRaisesRegex(ValueError,'exception'): self.render(data)

    def test_historical_six_story_edition_remains_renderable(self):
        data=edition_data(6); data.pop('story_count_policy'); data['date']='2026-10-18'
        self.assertEqual(len(self.render(data)[2]['stories']),6)

    def test_seven_story_render_rejected_even_with_exception(self):
        data=edition_data(7)
        data['six_story_exception']=dict(candidate_id=5,original_headline='AI launch 5',reason=REASON)
        with self.assertRaises(ValueError): self.render(data)


if __name__=='__main__': unittest.main()
