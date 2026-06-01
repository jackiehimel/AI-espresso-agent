/**
 * PR Impact Agent
 * ---------------
 * Analyzes the git diff of a pull request with Claude,
 * posts a structured comment to the PR, and optionally
 * blocks merge if critical issues are found.
 */

const { execSync } = require("child_process");
const path = require("path");
const Anthropic = require("@anthropic-ai/sdk");
const { Octokit } = require("@octokit/rest");

const BOT_COMMENT_MARKER = "<!-- impact-agent-comment -->";

const anthropic = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY });
const octokit = new Octokit({ auth: process.env.GITHUB_TOKEN });

function loadConfig() {
  try {
    const configPath = path.resolve(process.cwd(), "impact.config.json");
    return require(configPath);
  } catch {
    console.log("No impact.config.json found - using defaults.");
    return {
      stack: ["Python", "GitHub Actions"],
      blockOn: [
        "n_plus_one_queries",
        "unhandled_auth_exceptions",
        "exposed_secrets",
        "missing_input_validation",
        "unbounded_loops_over_db",
        "sql_injection_risk",
        "hardcoded_credentials",
      ],
      warnThresholds: {
        latency_ms: 75,
        bundle_kb: 30,
        new_deps: 3,
        min_coverage_pct: 80,
      },
      ignorePaths: [
        "editions/**",
        "agent/data/editions/**",
        "agent/data/archive.jsonl",
        "agent/data/sent_editions.json",
        "docs/**",
      ],
      blockMergeOnCritical: true,
      commentUpdateStrategy: "update_existing",
    };
  }
}

function getDiff(baseBranch) {
  try {
    execSync(`git fetch origin ${baseBranch}`, { stdio: "pipe" });
    const diff = execSync(`git diff origin/${baseBranch}...HEAD`, {
      stdio: "pipe",
      maxBuffer: 1024 * 1024 * 10,
    }).toString();

    if (!diff.trim()) {
      console.log("No diff found between branches.");
      return null;
    }

    const maxDiffChars = 30000;
    if (diff.length > maxDiffChars) {
      console.log(
        `Diff is large (${diff.length} chars) - truncating to ${maxDiffChars} chars.`,
      );
      return (
        diff.slice(0, maxDiffChars) + "\n\n[...diff truncated for token limit]"
      );
    }

    return diff;
  } catch (err) {
    console.error("Failed to get diff:", err.message);
    throw err;
  }
}

function buildSystemPrompt(config) {
  const stackContext = config.stack?.length
    ? `Tech stack: ${config.stack.join(", ")}.`
    : "";

  const blockPatterns = config.blockOn?.length
    ? `Always flag as critical (verdict: block) if you detect: ${config.blockOn.join(", ")}.`
    : "";

  const thresholds = config.warnThresholds
    ? `Warn if estimated latency impact > ${config.warnThresholds.latency_ms}ms, bundle size increase > ${config.warnThresholds.bundle_kb}KB, new deps > ${config.warnThresholds.new_deps}, test coverage < ${config.warnThresholds.min_coverage_pct}%.`
    : "";

  const ignorePaths = config.ignorePaths?.length
    ? `Ignore changes in: ${config.ignorePaths.join(", ")}.`
    : "";

  return `You are an expert code impact analysis bot embedded in a CI pipeline. Analyze git diffs and produce a structured, actionable impact report.

${stackContext}
${blockPatterns}
${thresholds}
${ignorePaths}

Analyze the diff provided by the user and return ONLY a valid JSON object with no markdown fencing.

Return this exact schema:
{
  "prTitle": "short inferred title for this change (max 8 words)",
  "summary": "2-3 sentence plain-English summary of what this PR does and its main risk",
  "riskLevel": "low" | "medium" | "high",
  "verdict": "approve" | "approve_with_suggestions" | "block",
  "verdictText": "one-line verdict explanation",
  "verdictSub": "short supporting detail (e.g. '2 critical issues - estimated fix: ~30 min')",
  "metrics": [
    { "label": "Latency impact", "value": "e.g. +110ms or Neutral", "trend": "up" | "down" | "neutral" },
    { "label": "DB query delta", "value": "e.g. N+1 or +2 queries", "trend": "up" | "down" | "neutral" },
    { "label": "Bundle size delta", "value": "e.g. +42 KB or Neutral", "trend": "up" | "down" | "neutral" },
    { "label": "Test coverage", "value": "e.g. 84% or Unknown", "trend": "up" | "down" | "neutral" }
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
  "positives": ["list of things done well in this PR - include at least one when applicable"]
}

Rules:
- verdict must be "block" if any issue has severity "critical"
- verdict must be "approve_with_suggestions" if there are warnings but no criticals
- verdict must be "approve" only if there are zero critical or warning issues
- Be specific and reference exact file names and symbols from the diff
- fixes.codeSnippet should be concrete and corrected, not pseudocode
- Keep descriptions concise and practical`;
}

async function analyseWithClaude(diff, config) {
  console.log("Sending diff to Claude for analysis...");

  const response = await anthropic.messages.create({
    model: "claude-sonnet-4-20250514",
    max_tokens: 2000,
    system: buildSystemPrompt(config),
    messages: [
      {
        role: "user",
        content: `Analyze this git diff:\n\n${diff}`,
      },
    ],
  });

  const text = response.content[0]?.text || "{}";

  try {
    return JSON.parse(text.replace(/```json|```/g, "").trim());
  } catch {
    console.error("Failed to parse Claude response as JSON:", text);
    throw new Error("Claude returned invalid JSON. Raw response logged above.");
  }
}

function formatComment(analysis) {
  const {
    summary,
    riskLevel,
    verdict,
    verdictText,
    verdictSub,
    metrics,
    issues,
    fixes,
    affectedServices,
    positives,
  } = analysis;

  const riskEmoji = { low: "🟢", medium: "🟡", high: "🔴" }[riskLevel] || "⚪";
  const verdictEmoji =
    verdict === "block" ? "🚫" : verdict === "approve" ? "✅" : "⚠️";
  const trendArrow = { up: "↑", down: "↓", neutral: "→" };
  const severityEmoji = { critical: "🔴", warning: "🟡", info: "🔵" };

  const criticalCount =
    issues?.filter((issue) => issue.severity === "critical").length || 0;
  const warningCount =
    issues?.filter((issue) => issue.severity === "warning").length || 0;
  const infoCount =
    issues?.filter((issue) => issue.severity === "info").length || 0;

  const metricsTable = metrics?.length
    ? `
| Metric | Value | Trend |
|--------|-------|-------|
${metrics.map((metric) => `| ${metric.label} | \`${metric.value}\` | ${trendArrow[metric.trend] || "→"} |`).join("\n")}
`
    : "";

  const issuesSection = issues?.length
    ? `
### 🔍 Issues found — ${criticalCount} critical · ${warningCount} warnings · ${infoCount} info

${issues
  .map(
    (issue) => `<details>
<summary>${severityEmoji[issue.severity]} <strong>${issue.title}</strong> <code>${issue.location}</code></summary>

${issue.description}

</details>`,
  )
  .join("\n\n")}
`
    : "\n### ✅ No issues detected\n";

  const fixesSection = fixes?.length
    ? `
### ⚡ Suggested fixes

${fixes
  .map(
    (fix, index) => `**${index + 1}. ${fix.title}**
${fix.description}
${fix.codeSnippet ? `\`\`\`\n${fix.codeSnippet}\n\`\`\`` : ""}`,
  )
  .join("\n\n")}
`
    : "";

  const servicesSection = affectedServices?.length
    ? `**Affected files/services:** ${affectedServices.map((service) => `\`${service}\``).join(", ")}\n`
    : "";

  const positivesSection = positives?.length
    ? `
### 👍 What's good

${positives.map((positive) => `- ${positive}`).join("\n")}
`
    : "";

  return `## ${verdictEmoji} Impact Agent Report — ${riskEmoji} ${String(riskLevel || "unknown").toUpperCase()} RISK

> ${summary || "No summary provided."}

${servicesSection}
${metricsTable}
${issuesSection}
${fixesSection}
${positivesSection}

---

### Verdict: ${verdictText || "No verdict text provided"}
${verdictSub ? `*${verdictSub}*` : ""}

${verdict === "block"
    ? "> ❌ **This PR is blocked from merging.** Please address critical issues above."
    : verdict === "approve_with_suggestions"
      ? "> ⚠️ **Suggestions above are non-blocking** - address them when practical."
      : "> ✅ **No blocking issues found.** Good to merge."}

<sub>Posted by impact-agent · Powered by Claude · [Configure rules](../../blob/main/impact.config.json)</sub>
${BOT_COMMENT_MARKER}`;
}

async function findExistingComment(owner, repo, prNumber) {
  const { data: comments } = await octokit.issues.listComments({
    owner,
    repo,
    issue_number: prNumber,
    per_page: 100,
  });

  return comments.find(
    (comment) =>
      comment.user?.type === "Bot" &&
      comment.body?.includes(BOT_COMMENT_MARKER),
  );
}

async function postComment(owner, repo, prNumber, body, updateStrategy) {
  if (updateStrategy === "update_existing") {
    const existing = await findExistingComment(owner, repo, prNumber);
    if (existing) {
      console.log(`Updating existing comment #${existing.id}...`);
      await octokit.issues.updateComment({
        owner,
        repo,
        comment_id: existing.id,
        body,
      });
      return;
    }
  }

  console.log("Posting new PR comment...");
  await octokit.issues.createComment({
    owner,
    repo,
    issue_number: prNumber,
    body,
  });
}

async function main() {
  const repo = process.env.REPO;
  const prNumber = parseInt(process.env.PR_NUMBER || "", 10);
  const baseBranch = process.env.BASE_BRANCH || "main";

  if (!process.env.ANTHROPIC_API_KEY) {
    throw new Error("Missing ANTHROPIC_API_KEY secret.");
  }
  if (!repo || Number.isNaN(prNumber)) {
    throw new Error("Missing required env vars: REPO and PR_NUMBER.");
  }

  const [owner, repoName] = repo.split("/");
  const config = loadConfig();

  console.log(`Analyzing PR #${prNumber} in ${repo}`);
  console.log(`Base branch: ${baseBranch}`);

  const diff = getDiff(baseBranch);
  if (!diff) {
    console.log("No diff to analyze. Skipping.");
    return;
  }

  console.log(`Diff size: ${diff.length} chars`);

  const analysis = await analyseWithClaude(diff, config);
  const commentBody = formatComment(analysis);
  await postComment(
    owner,
    repoName,
    prNumber,
    commentBody,
    config.commentUpdateStrategy,
  );

  if (config.blockMergeOnCritical && analysis.verdict === "block") {
    const criticalIssues =
      analysis.issues?.filter((issue) => issue.severity === "critical") || [];
    console.error(
      `Blocking merge - ${criticalIssues.length} critical issue(s) found.`,
    );
    process.exit(1);
  }

  console.log("Impact analysis complete - no blocking issues.");
}

main().catch((err) => {
  console.error("Impact agent failed:", err.message);
  process.exit(1);
});
