#!/bin/bash
# Hermes gateway 看门狗：微信长轮询在进程内持续失败时自动重启 gateway。
#
# 背景：macOS 上长驻进程在网络切换（睡眠唤醒/代理切换）后解析状态会失效，
# 表现为 ~/.hermes/logs/errors.log 持续出现 "[Weixin] poll error"，而同机
# 新进程解析与直连都正常；重启 gateway 即恢复，但数小时后会复发
# （2026-10-12 实测两次：10:16 启动 12:26 坏、12:50 启动 14:51 坏）。
# 本脚本由 ai.news-agent.gateway-watchdog LaunchAgent 每 60 秒调用一次，
# 连续两次检出"120 秒内的 poll error"即 kickstart gateway 实现自愈，
# 两次重启之间至少间隔冷却期，防止重启风暴。
#
# 可用环境变量：ERRORS_LOG / STATE_FILE / ACTION_LOG / RECENT_SECONDS /
# STREAK_THRESHOLD / COOLDOWN_SECONDS / DRY_RUN（=1 时只打印动作不真正重启）
set -uo pipefail

ERRORS_LOG="${ERRORS_LOG:-$HOME/.hermes/logs/errors.log}"
STATE_FILE="${STATE_FILE:-$HOME/.hermes/gateway-watchdog.state}"
ACTION_LOG="${ACTION_LOG:-$HOME/.hermes/logs/gateway-watchdog.log}"
RECENT_SECONDS="${RECENT_SECONDS:-120}"
STREAK_THRESHOLD="${STREAK_THRESHOLD:-2}"
COOLDOWN_SECONDS="${COOLDOWN_SECONDS:-900}"
DRY_RUN="${DRY_RUN:-0}"

now=$(date +%s)

# errors.log 里最新一条 weixin poll error 距现在的秒数；无记录返回大数。
last_poll_error_age() {
  local line ts
  line=$(grep 'poll error' "$ERRORS_LOG" 2>/dev/null | tail -1)
  [[ -n "$line" ]] || { echo 999999; return 0; }
  ts=$(date -j -f "%Y-%m-%d %H:%M:%S" \
        "$(awk '{print $1, $2}' <<<"$line" | cut -d, -f1)" +%s 2>/dev/null)
  ts=${ts:-0}
  echo $((now - ts))
}

streak=0
last_restart=0
if [[ -r "$STATE_FILE" ]]; then
  read -r streak last_restart < "$STATE_FILE" 2>/dev/null || { streak=0; last_restart=0; }
fi
streak=${streak:-0}
last_restart=${last_restart:-0}

age=$(last_poll_error_age)
if (( age <= RECENT_SECONDS )); then
  streak=$((streak + 1))
else
  streak=0
fi

if (( streak >= STREAK_THRESHOLD && now - last_restart >= COOLDOWN_SECONDS )); then
  msg="$(date '+%Y-%m-%d %H:%M:%S') restart gateway: poll error age=${age}s streak=$streak"
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "DRY_RUN $msg"
  else
    echo "$msg" >> "$ACTION_LOG"
    launchctl kickstart -k "gui/$(id -u)/ai.hermes.gateway"
  fi
  last_restart=$now
  streak=0
fi

printf '%s %s\n' "$streak" "$last_restart" > "$STATE_FILE"
exit 0
