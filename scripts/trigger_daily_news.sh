#!/bin/bash
# 北京时间 12:00 精准触发 GitHub Actions 推送（repository_dispatch）。
#
# 背景：GitHub Actions schedule 在高峰期会延迟数小时甚至整批丢弃
# （2026-10-19 周一实测：12:00 主梯队与 12:30–15:30 补跑梯队全部缺席，
# 16:30 兜底梯队延迟到 17:41 才触发，治理板块 17:42 才推送）。
# 本脚本由 ai.news-agent.daily-trigger LaunchAgent 在 12:00 / 12:30 / 13:30
# 调用（机器时区 Asia/Shanghai，即北京时间；睡眠唤醒后 launchd 会补跑错过的时间点）：
#   1. 今天已有成功运行 → 已推送（或守卫空跑），直接跳过；
#   2. 否则 POST repository_dispatch，workflow 内还有 --skip-if-ran-today 双保险，
#      一天最多推一次。
# 触发本身在 GitHub 云上执行，本机只需能联网调用 GitHub API（gh 已带 repo 权限）。
set -uo pipefail

REPO="chenliang613/News-Agent"
TODAY="$(date +%F)"
STAMP="[$(date '+%F %T')]"

if ! gh auth status >/dev/null 2>&1; then
  echo "$STAMP ERROR: gh 未登录，无法触发" >&2
  exit 1
fi

success_count=$(gh run list --repo "$REPO" --workflow daily.yml \
  --created ">=$TODAY" --json conclusion \
  --jq '[.[] | select(.conclusion=="success")] | length' 2>/dev/null)
success_count=${success_count:-0}

if [[ "$success_count" -gt 0 ]]; then
  echo "$STAMP 今天已有 $success_count 次成功运行，跳过触发"
  exit 0
fi

echo "$STAMP 触发 repository_dispatch daily-news ..."
gh api "repos/$REPO/dispatches" -X POST -f event_type=daily-news
echo "$STAMP 已触发，约 1 分钟内可在 Actions 页面看到运行"
