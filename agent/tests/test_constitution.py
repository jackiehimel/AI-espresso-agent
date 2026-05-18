"""Tests for agent/constitution.py — editorial backstop (Venu alignment)."""

from __future__ import annotations

import unittest

from constitution import (
    constitution_violations,
    is_ai_load_bearing,
    is_celebration_tone,
    passes_constitution,
)


class CompositionTableTests(unittest.TestCase):
  """Spec composition: load-bearing AND celebration."""

  def test_waymo_trapped_reject(self):
    h = "Waymo driverless cars become trapped in Atlanta suburb after glitch"
    self.assertFalse(is_ai_load_bearing(h))
    self.assertFalse(is_celebration_tone(h))
    self.assertFalse(passes_constitution(h))
    self.assertTrue(constitution_violations(h))

  def test_chatgpt_bank_accept(self):
    h = "ChatGPT can now look at your bank account"
    self.assertTrue(is_ai_load_bearing(h))
    self.assertTrue(is_celebration_tone(h))
    self.assertTrue(passes_constitution(h))
    self.assertFalse(constitution_violations(h))

  def test_ai_nav_fails_reject(self):
    h = "AI navigation system fails again"
    self.assertFalse(is_ai_load_bearing(h))
    self.assertFalse(is_celebration_tone(h))
    self.assertFalse(passes_constitution(h))

  def test_anthropic_office_reject(self):
    h = "Anthropic opens Singapore office"
    self.assertFalse(is_ai_load_bearing(h))
    self.assertTrue(is_celebration_tone(h))
    self.assertFalse(passes_constitution(h))

  def test_codex_mobile_accept(self):
    h = "OpenAI brings its Codex coding app to mobile"
    self.assertTrue(passes_constitution(h))


class CelebrationToneTests(unittest.TestCase):
  def test_researcher_poach_ok(self):
    h = "OpenAI loses senior researchers to Anthropic"
    self.assertTrue(is_celebration_tone(h))
    self.assertTrue(is_ai_load_bearing(h))

  def test_claude_refuses_reject(self):
    h = "Anthropic's Claude refuses to help, breaking workflow for X"
    self.assertFalse(is_celebration_tone(h))
    self.assertFalse(is_ai_load_bearing(h))

  def test_ai_slop_bug_bounty_reject(self):
    h = "Never-ending AI slop strains corporate hacking reward schemes"
    self.assertFalse(is_celebration_tone(h))


class LoadBearingTests(unittest.TestCase):
  def test_cftc_polymarket_accept(self):
    h = "CFTC is using AI to spot insider trading on Polymarket and Kalshi"
    self.assertTrue(is_ai_load_bearing(h))

  def test_hbr_sociology_reject(self):
    h = "AI is reshaping how companies hire"
    self.assertFalse(passes_constitution(h))


if __name__ == "__main__":
  unittest.main()
