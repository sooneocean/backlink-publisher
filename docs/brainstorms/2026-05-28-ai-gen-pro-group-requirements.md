---
date: 2026-05-28
topic: ai-gen-pro-group
---

# AI 生成功能折疊為 Pro Mode 分組

## Problem Frame

Settings 頁的 LLM 整合 card 現在把「連線設定」和「AI 生成功能」平鋪在同一層。
普通用戶只需配置連線，看到 AI 全文生成和 AI 封面生成的選項會造成視覺噪音。
Pro 功能需要折疊收納，讓非 Pro 用戶的 settings 頁保持簡潔。

「Pro Mode」是 UI 組織標籤，不是功能門禁——任何用戶都可以手動展開並啟用；折疊純粹是視覺收納。

## Requirements

**Pro Mode 分組**
- R1. 在現有 LLM card 內，將以下兩項包進一個可折疊子區塊（Bootstrap collapse，`id="llm-pro-mode-collapse"`）：
  - AI 內容生成引擎（`use_article_gen` toggle 開關）
  - AI Banner 生成（`use_image_gen` toggle + `image_gen_api_key` 輸入）
  - 注意：`article_system_prompt` 不在此次範圍，後端 save route 目前不讀取它。
- R2. 子區塊 body 預設折疊（`class="collapse"`）；折疊標頭列（header row）始終可見。
- R3. 若任一 Pro 功能已啟用（`use_article_gen` 或 `use_image_gen` 為 true），頁面載入時 body 自動展開。
  - 實作：Jinja2 條件 class：`class="collapse {% if llm_settings.use_article_gen or llm_settings.use_image_gen %}show{% endif %}"`
  - 適用範圍：每次 GET 請求（包含 form POST 後的 redirect）——由 server-rendered 初始 class 控制，不需 JS。
- R4. 折疊標頭顯示「Pro Mode AI 生成」文字 + `<span class="badge bg-warning text-dark">Pro</span>` 徽章 + 右側 `bi-chevron-down` 箭頭（展開時朝上，用 CSS `rotate(-180deg)` + `transition`）。
  - 整個標頭列為 `<button data-bs-toggle="collapse" data-bs-target="#llm-pro-mode-collapse">`，確保足夠點擊面積。
- R5. 不改動任何 Python 後端、路由、表單 field name；表單結構保持不變，僅移動 HTML 層級。
  - Bootstrap collapse 是 CSS-only 可見性控制，折疊狀態下勾選的 checkbox 仍會 POST——此為預期行為，不需 `disabled` 屬性。

**正常用戶體驗**
- R6. 連線設定（endpoint / api_key / model / temperature / system_prompt）維持在 collapse 外，始終可見。
- R7. 若兩個 Pro toggle 都是 false，Pro Mode 標頭行可見但 body 折疊隱藏，普通用戶看不到 Pro 欄位內容。

## Success Criteria

- 未啟用任何 Pro 功能時：標頭可見，body 折疊，Pro 欄位不顯示。
- 任一 Pro 功能已啟用時：頁面載入自動展開，顯示已配置狀態。
- 用戶停用兩個 Pro toggle 並存儲後，下次載入 body 重新折疊。
- 表單 POST 後欄位值正確保存（smoke-test 驗證，後端行為不變）。

## Scope Boundaries

- 僅改 `_settings_llm_integration.html`，不動後端 Python 和測試。
- `article_system_prompt` 不在此次 UI 範圍（後端不讀取 form field，加了也會被靜默丟棄）。
- 不新增任何 Pro 門禁邏輯；折疊只做視覺收納。

## Key Decisions

- **標頭始終可見**：Bootstrap 標準折疊行為，header 保留，body hidden。非 Pro 用戶看到標頭可以探索進階功能。
- **Jinja2 class 控制展開**（不用 JS）：`show` class 由 server-rendered 初始值決定，無 JS 初始化競態問題。
- **Collapse ID 固定**：`llm-pro-mode-collapse`，避免與 `settings_main.js` hash-fragment 邏輯衝突。
- **整列可點擊**：`<button data-bs-toggle>` 包住整個標頭，確保觸控目標足夠大。
- **`article_system_prompt` 移出範圍**：後端未接通，加 textarea 會靜默丟失用戶輸入。
- **不做 feature flag / 權限控制**：折疊是視覺組織，不是功能鎖定。

## Next Steps

→ `/ce:work` 直接實作（template-only 修改，範圍清晰）
