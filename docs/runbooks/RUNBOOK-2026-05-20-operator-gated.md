# Operator Runbook — 2026-05-20

4 個 operator-gated 工作。每段給：**為何 / 命令 / 觀察點 / Pass-Fail / 下一步**。

照打。完成一項就 ping 我「#107 PASS」「Phase 3 結果：ghpages PASS / hashnode FAIL / writeas PASS」等。

---

## 1. PR #107 second-bind 驗證

**為何**：PR #107 把 bind-channel 改用 `launch_persistent_context` 共享 user_data_dir，目的是讓 OAuth 提供者（Google/GitHub/Facebook）看到穩定 fingerprint，第二次 bind 不再要求重登 + 2FA。需 operator 物理機驗證才能 merge。

**命令**：

```bash
cd "/Users/dex/YDEX/INPORTANT WORK/外链/backlink-publisher/bp-main-validate"

# 確保乾淨 profile（強制第一次 bind 完整走 OAuth）
rm -rf ~/.config/backlink-publisher/browser-profile

# 第一次 bind — 你會看到 Google OAuth + 2FA，正常走完
PYTHONPATH=src python -m backlink_publisher.cli.bind_channel --channel velog
# 等命令成功退出（"velog bound" 之類訊息），瀏覽器自動關閉

# 第二次 bind — 立刻、不要等
PYTHONPATH=src python -m backlink_publisher.cli.bind_channel --channel velog
```

**觀察點**（第二次 bind 期間）：
- 瀏覽器開啟後是否**立即停在 velog 已登入頁面**？
- 還是又跳出 Google 帳號選擇 / 2FA 提示？

**Pass-Fail**：
- ✅ PASS：第二次無 OAuth 提示、無 2FA、直接顯示 velog feed
- ❌ FAIL：又被要求登入 → persistent profile 沒生效 → 我來查 `browser-profile/` 內容診斷

**下一步**：
- PASS：`gh pr merge 107 --squash --delete-branch`（或叫我來 merge）
- FAIL：告訴我，我從 driver.py:421-446 那段查 `user_data_dir` 寫入邏輯

---

## 2. Phase 3 三平台 dofollow hand-check（unblock Phase 4）

**為何**：ghpages（#96）/ hashnode（#102）/ writeas（#103）三個 adapter 已 land，但沒人驗證它們發出的真實文章上「目標連結」是否帶 `rel="nofollow"`。≥2/3 dofollow 才開 Phase 4。

**前置**：三個平台都已 bind（運行 `bind-channel --channel ghpages` / `hashnode` / `writeas` 至少一次）。

**命令**：

```bash
cd "/Users/dex/YDEX/INPORTANT WORK/外链/backlink-publisher/backlink-publisher"

# 準備 1 條 seed × 3 平台
cat > /tmp/dofollow-test-seeds.jsonl <<'EOF'
{"language":"en","tier":"1","channel":"ghpages","target_url":"https://example.com/","anchor_text":"example reference","title_hint":"Phase 3 dofollow check"}
{"language":"en","tier":"1","channel":"hashnode","target_url":"https://example.com/","anchor_text":"example reference","title_hint":"Phase 3 dofollow check"}
{"language":"en","tier":"1","channel":"writeas","target_url":"https://example.com/","anchor_text":"example reference","title_hint":"Phase 3 dofollow check"}
EOF

# Pipeline
cat /tmp/dofollow-test-seeds.jsonl \
  | plan-backlinks \
  | validate-backlinks \
  | publish-backlinks \
  > /tmp/published.jsonl

# 列出三個發佈結果的 URL
jq -r 'select(.status=="published") | "\(.channel // .platform): \(.article_urls[0] // .url)"' /tmp/published.jsonl
```

**驗證 dofollow**（對 jq 列出的 3 個 URL 各跑一次）：

```bash
# 替換 ARTICLE_URL 為實際發佈 URL
ARTICLE_URL="https://...你發佈的文章 URL..."

# 抓含 example.com 連結的 <a> 標籤
curl -sL "$ARTICLE_URL" | grep -oE '<a[^>]*href="[^"]*example\.com[^"]*"[^>]*>' | head -3
```

**Pass-Fail**（針對每個平台）：
- ✅ dofollow（PASS）：輸出的 `<a>` 標籤 **沒有** `rel="nofollow"` / `rel="ugc"` / `rel="sponsored"`，或 `rel` 屬性完全不存在
- ❌ nofollow（FAIL）：`<a>` 帶 `rel="nofollow"`（或 ugc/sponsored）

**整體 Gate**：≥ 2/3 PASS → Phase 4 GO；< 2/3 → 三 adapter 需要查發佈時是否塞了 `<a rel="...">`，可能 platform 自動加 nofollow（hashnode / writeas 對 external link 常見此行為）

**下一步**：
- ≥ 2/3 PASS：ping 我「Phase 3 GO，ghpages/hashnode/writeas dofollow=X/Y/Z」
- < 2/3 PASS：ping 我哪個 FAIL，我查該 adapter 的 HTML 構造代碼

---

## 3. Unit 6d Medium 3-mode

**為何**：Unit 6d 是 Medium adapter 三模式（API / Browser / Hybrid）切換邏輯，需 `medium-cookies.json` 存在才能驗。PR #88 hard-cut 之後，operator 需重跑一次 medium-login 取得 cookies-only 格式。

**前置**：MUST do — Medium 登入後 Phase 0 spike 才能跑。

**命令**：

```bash
cd "/Users/dex/YDEX/INPORTANT WORK/外链/backlink-publisher/backlink-publisher"

# 確認舊格式不存在或備份（PR #88 hard-cut 不再讀 storage_state.json）
ls -l ~/.config/backlink-publisher/medium-* 2>&1

# 跑 medium-login（瀏覽器開啟，完整走 Medium OAuth）
medium-login
# 或：PYTHONPATH=src python -m backlink_publisher.cli.medium_login

# 驗證輸出
ls -l ~/.config/backlink-publisher/medium-cookies.json
stat -f "%Sp" ~/.config/backlink-publisher/medium-cookies.json  # 應是 -rw-------（0600）
cat ~/.config/backlink-publisher/medium-meta.json  # UA + chromium_version + login_at
```

**Pass-Fail**：
- ✅ PASS：`medium-cookies.json` 存在、0600、`medium-meta.json` 含 UA
- ❌ FAIL：cookies 檔不存在或非 0600 → ping 我

**下一步（PASS 後）**：ping 我「Medium bound，proceed Unit 6d」我接手實作 3-mode dispatcher（純 code 工作，無需 operator 介入）

---

## 4. Phase 4 Dev.to / WP.com / Mastodon 模板

**為何**：三新 adapter 跟 Phase 3 三 adapter 同 pattern（`_required_headers()` helper + token file 0600 + REST publish + live verify endpoint）。Plan 文件已備（per memory），只等 Phase 3 dofollow GO 才開工。

**前置**：#2 完成且 ≥ 2/3 PASS。

**Operator 動作**：無 — 純 code 工作，我接手後：

1. 開新 worktree `bp-phase4-cluster`
2. 三 adapter scaffold 跟 ghpages/hashnode/writeas 直接複製改 endpoint + auth dialect
3. 每平台一個 token file（`devto-token.json` / `wpcom-token.json` / `mastodon-token.json`），SEC-3 0600
4. 註冊到 `publishing/adapters/__init__.py`
5. 三 PR 或一 PR（按你偏好）
6. Operator 後續 bind 三平台（拿 dev.to API key / WP.com OAuth / Mastodon access token）

**下一步**：ping 我「Phase 3 GO，開 Phase 4」+ 你偏好「1 PR 還是 3 PR」

---

## 一句話 status board

| # | Title | 卡在哪 | 我能否協助 |
|---|---|---|---|
| 1 | #107 second-bind | 你跑兩次 bind 比對 | merge 或 fix |
| 2 | Phase 3 dofollow | 你發 3 篇 + curl 驗 | adapter HTML fix（如 FAIL） |
| 3 | Medium bound | 你跑 medium-login | Unit 6d 全代碼 |
| 4 | Phase 4 | #2 GO | 全代碼 |

跑完任何一項回我結果，後續我接手。
