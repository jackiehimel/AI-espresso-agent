/**
 * PR Impact Agent
 * ---------------
 * Analyses the git diff of a pull request using Claude,
 * posts a structured comment to the PR, and optionally
 * blocks the merge if critical issues are found.
 *
 * Required env vars:
 *   ANTHROPIC_API_KEY  — your Anthropic API key
 *   GITHUB_TOKEN       — injected automatically by GitHub Actions
 *   PR_NUMBER          — PR number (injected by workflow)
 *   REPO               — "owner/repo" (injected by workflow)
 *   BASE_BRANCH        — base branch name (injected by workflow)
 */

const { execSync } = require('child_process');
const path = require('path');
const Anthropic = require('@anthropic-ai/sdk');
const { Octokit } = require('@octokit/rest');

// ─── Clients ────────────────────────────────────────────────────────────────

const anthropic = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY });
const octokit = new Octokit({ auth: process.env.GITHUB_TOKEN });

// ─── Load config ────────────────────────────────────────────────────────────

function loadConfig() {
  try {
    const configPath = path.resolve(process.cwd(), 'impact.config.json');
    return require(configPath);
  } catch {
    console.log('ℹ No impact.config.json found — using defaults.');
    return {
      stack: [],
      blockOn: [
        'n_plus_one_queries',
        'unhandled_auth_exceptions',
        'exposed_secrets',
        'missing_input_validation',
        'unbounded_loops_over_db'
      ],
      warnThresholds: {
        latency_ms: 50,
        bundle_kb: 30,
        new_deps: 3,
        min_coverage_pct: 80
      },
      ignorePaths: ['**/*.test.ts', '**/*.spec.ts', '**/migrations/**', 'docs/**'],
      blockMergeOnCritical: true,
      commentUpdateStrategy: 'update_existing'
    };
  }
}

// ─── Get diff ────────────────────────────────────────────────────────────────

function getDiff(baseBranch) {
  try {
    // Fetch the base branch so we can diff against it
    execSync(`git fetch origin ${baseBranch}`, { stdio: 'pipe' });
    const diff = execSync(`git diff origin/${baseBranch}...HEAD`, {
      stdio: 'pipe',
      maxBuffer: 1024 * 1024 * 10 // 10MB max
    }).toString();

    if (!diff.trim()) {
      console.log('ℹ No diff found between branches.');
      return null;
    }

    // Truncate if very large to stay within token limits
    const MAX_DIFF_CHARS = 30000;
    if (diff.length > MAX_DIFF_CHARS) {
      console.log(`⚠ Diff is large (${diff.length} chars) — truncating to ${MAX_DIFF_CHARS} chars.`);
      return diff.slice(0, MAX_DIFF_CHARS) + '\n\n[...diff truncated for token limit]';
    }

    return diff;
  } catch (err) {
    console.error('Failed to get diff:', err.message);
    throw err;
  }
}

// ─── Build system prompt ─────────────────────────────────────────────────────

function buildSystemPrompt(config) {
  const stackContext = config.stack?.length
    ? `Tech stack: ${config.stack.join(', ')}.`
    : '';

  const blockPatterns = config.blockOn?.length
    ? `Always flag as critical (verdict: block) if you detect: ${config.blockOn.join(', ')}.`
    : '';

  const thresholds = config.warnThresholds
    ? `Warn if estimated latency impact > ${config.warnThresholds.latency_ms}ms, bundle size increase > ${config.warnThresholds.bundle_kb}KB, new deps > ${config.warnThresholds.new_deps}, test coverage < ${config.warnThresholds.min_coverage_pct}%.`
    : '';

  const ignorePaths = config.ignorePaths?.length
    ? `Ignore changes in: ${config.ignorePaths.join(', ')}.`
    : '';

  return `You are an expert code impact analysis bot embedded in a CI/CD pipeline. Your job is to analyse git diffs and produce structured, actionable impact reports.

${stackContext}
${blockPatterns}
${thresholds}
${ignorePaths}

Analyse the diff provided by the user and return ONLY a valid JSON object — no markdown, no backticks, no preamble, no explanation outside the JSON.

Return this exact schema:
{
  "prTitle": "short inferred title for this change (max 8 words)",
  "summary": "2-3 sentence plain-English summary of what this PR does and its main risk",
  "riskLevel": "low" | "medium" | "high",
  "verdict": "approve" | "approve_with_suggestions" | "block",
  "verdictText": "one-line verdict explanation",
  "verdictSub": "short supporting detail (e.g. '2 critical issues · estimated fix: ~30 min')",
  "metrics": [
    { "label": "Latency impact",    "value": "e.g. +110ms or Neutral", "trend": "up" | "down" | "neutral" },
    { "label": "DB query delta",    "value": "e.g. N+1 or +2 queries", "trend": "up" | "down" | "neutral" },
    { "label": "Bundle size delta", "value": "e.g. +42 KB or Neutral",  "trend": "up" | "down" | "neutral" },
    { "label": "Test coverage",     "value": "e.g. 84% or Unknown",     "trend": "up" | "down" | "neutral" }
  ],
  "issues": [
    {
      "severity": "critical" | "warning" | "info",
      "title": "short issue title",
      "description": "clear explanation of the issue and why it matters",
      "location": "filename:line or 'migration needed' or similar"
    }
  ],
  "fixes": [
    {
      "title": "fix title",
      "description": "what to do and why",
      "codeSnippet": "short illustrative code snippet (optional, can be empty string)"
    }
  ],
  "affectedServices": ["list", "of", "affected", "files", "or", "services"],
  "positives": ["list of things done well in this PR — always include at least one if applicable"]
}

Rules:
- verdict must be "block" if any issue has severity "critical"
- verdict must be "approve_with_suggestions" if there are warnings but no criticals
- verdict must be "approve" only if there are zero critical or warning issues
- Be specific — reference exact file names, line numbers, and variable names from the diff
- fixes.codeSnippet should be a concrete corrected snippet, not pseudocode
- Keep descriptions concise and developer-friendly — no corporate fluff
- positives is important for morale — acknowledge clean patterns, good test coverage, good naming etc.`;
}

// ─── Call Claude ─────────────────────────────────────────────────────────────

async function analyseWithClaude(diff, config) {
  console.log('🤖 Sending diff to Claude for analysis...');
  const model =
    process.env.ANTHROPIC_MODEL ||
    config.anthropicModel ||
    'claude-sonnet-4-20250514';
  console.log(`🧠 Using model: ${model}`);

  const response = await anthropic.messages.create({
    model,
    max_tokens: 2000,
    system: buildSystemPrompt(config),
    messages: [
      {
        role: 'user',
        content: `Analyse this git diff:\n\n${diff}`
      }
    ]
  });

  const text = response.content[0]?.text || '{}';

  try {
    return JSON.parse(text.replace(/```json|```/g, '').trim());
  } catch (parseErr) {
    console.error('Failed to parse Claude response as JSON:', text);
    throw new Error('Claude returned invalid JSON. Raw response logged above.');
  }
}

// ─── Format comment ──────────────────────────────────────────────────────────

function formatComment(analysis) {
  const {
    summary, riskLevel, verdict, verdictText, verdictSub,
    metrics, issues, fixes, affectedServices, positives
  } = analysis;

  const riskEmoji   = { low: '🟢', medium: '🟡', high: '🔴' }[riskLevel] || '⚪';
  const verdictEmoji = verdict === 'block' ? '🚫' : verdict === 'approve' ? '✅' : '⚠️';
  const trendArrow   = { up: '↑', down: '↓', neutral: '→' };

  // Severity counts
  const criticalCount = issues?.filter(i => i.severity === 'critical').length || 0;
  const warningCount  = issues?.filter(i => i.severity === 'warning').length  || 0;
  const infoCount     = issues?.filter(i => i.severity === 'info').length     || 0;

  const sevEmoji = { critical: '🔴', warning: '🟡', info: '🔵' };

  // Metrics table
  const metricsTable = metrics?.length ? `
| Metric | Value | Trend |
|--------|-------|-------|
${metrics.map(m => `| ${m.label} | \`${m.value}\` | ${trendArrow[m.trend] || '→'} |`).join('\n')}
` : '';

  // Issues section
  const issuesSection = issues?.length ? `
### 🔍 Issues found — ${criticalCount} critical · ${warningCount} warnings · ${infoCount} info

${issues.map(i => `<details>
<summary>${sevEmoji[i.severity]} <strong>${i.title}</strong> <code>${i.location}</code></summary>

${i.description}

</details>`).join('\n\n')}
` : `\n### ✅ No issues detected\n`;

  // Fixes section
  const fixesSection = fixes?.length ? `
### ⚡ Suggested fixes

${fixes.map((f, idx) => `**${idx + 1}. ${f.title}**
${f.description}
${f.codeSnippet ? `\`\`\`\n${f.codeSnippet}\n\`\`\`` : ''}`).join('\n\n')}
` : '';

  // Affected services
  const servicesSection = affectedServices?.length
    ? `**Affected files/services:** ${affectedServices.map(s => `\`${s}\``).join(', ')}\n`
    : '';

  // Positives
  const positivesSection = positives?.length ? `
### 👍 What's good

${positives.map(p => `- ${p}`).join('\n')}
` : '';

  return `## ${verdictEmoji} Impact Agent Report — ${riskEmoji} ${riskLevel.toUpperCase()} RISK

> ${summary}

${servicesSection}
${metricsTable}
${issuesSection}
${fixesSection}
${positivesSection}

---

### Verdict: ${verdictText}
${verdictSub ? `*${verdictSub}*` : ''}

${verdict === 'block'
  ? '> ❌ **This PR is blocked from merging.** Please address critical issues above.'
  : verdict === 'approve_with_suggestions'
  ? '> ⚠️ **Suggestions above are non-blocking** — address them when you can.'
  : '> ✅ **No blocking issues found.** Good to merge.'}

<sub>🤖 Posted by [impact-agent](https://github.com/marketplace) · Powered by Claude · [Configure rules](../../blob/main/impact.config.json)</sub>`;
}

// ─── Find existing bot comment ────────────────────────────────────────────────

async function findExistingComment(owner, repo, prNumber) {
  const { data: comments } = await octokit.issues.listComments({
    owner, repo, issue_number: prNumber, per_page: 100
  });

  return comments.find(
    c => c.user?.type === 'Bot' && c.body?.includes('Impact Agent Report')
  );
}

// ─── Post or update comment ───────────────────────────────────────────────────

async function postComment(owner, repo, prNumber, body, updateStrategy) {
  if (updateStrategy === 'update_existing') {
    const existing = await findExistingComment(owner, repo, prNumber);
    if (existing) {
      console.log(`📝 Updating existing comment #${existing.id}...`);
      await octokit.issues.updateComment({
        owner, repo, comment_id: existing.id, body
      });
      return;
    }
  }

  console.log('💬 Posting new PR comment...');
  await octokit.issues.createComment({
    owner, repo, issue_number: prNumber, body
  });
}

// ─── Main ────────────────────────────────────────────────────────────────────

async function main() {
  const repo      = process.env.REPO;
  const prNumber  = parseInt(process.env.PR_NUMBER, 10);
  const baseBranch = process.env.BASE_BRANCH || 'main';

  if (!repo || !prNumber) {
    throw new Error('Missing required env vars: REPO and PR_NUMBER must be set.');
  }

  const [owner, repoName] = repo.split('/');
  const config = loadConfig();

  console.log(`\n🔍 Analysing PR #${prNumber} in ${repo}...`);
  console.log(`📋 Base branch: ${baseBranch}`);

  // 1. Get diff
  const diff = getDiff(baseBranch);
  if (!diff) {
    console.log('✅ No diff to analyse. Skipping.');
    return;
  }

  console.log(`📄 Diff size: ${diff.length} chars`);

  // 2. Analyse with Claude
  const analysis = await analyseWithClaude(diff, config);

  console.log(`\n📊 Analysis complete:`);
  console.log(`   Risk level : ${analysis.riskLevel}`);
  console.log(`   Verdict    : ${analysis.verdict}`);
  console.log(`   Issues     : ${analysis.issues?.length || 0} (${analysis.issues?.filter(i=>i.severity==='critical').length||0} critical)`);

  // 3. Format and post comment
  const commentBody = formatComment(analysis);
  await postComment(owner, repoName, prNumber, commentBody, config.commentUpdateStrategy);
  console.log('✅ Comment posted successfully.');

  // 4. Block merge if critical issues and config says to
  if (config.blockMergeOnCritical && analysis.verdict === 'block') {
    const criticalIssues = analysis.issues?.filter(i => i.severity === 'critical') || [];
    console.error(`\n❌ BLOCKING MERGE — ${criticalIssues.length} critical issue(s) found:`);
    criticalIssues.forEach(i => console.error(`   • ${i.title} (${i.location})`));
    process.exit(1);
  }

  console.log('\n✅ Impact analysis complete — no blocking issues.');
}

main().catch(err => {
  console.error('💥 Impact agent failed:', err.message);
  process.exit(1);
});
