# 模擬持倉策略/知識參考清單

2026-07-30 為了完善模擬持倉(`ai_insight.build_trade_decision()`)的判斷品質，
搜尋GitHub找到的開源專案，記錄下來供之後參考、評估要不要引入邏輯或作法。
純參考清單，未包含任何程式碼依賴。

## LLM Agent 交易框架(架構上最相關——本專案也是用LLM讀報告做決策)

- [TauricResearch/TradingAgents](https://github.com/tauricresearch/tradingagents)
  ——多Agent LLM交易框架,拆成基本面/情緒/技術面分析師+交易員+風控團隊分工,
  支援Google/Anthropic等多家LLM。本專案目前`build_trade_decision()`是單一
  prompt要AI一次決定所有事,這個框架的「分工/分階段」模式可參考。
- [georgezouq/awesome-ai-in-finance](https://github.com/georgezouq/awesome-ai-in-finance)
  ——LLM/深度學習金融策略總覽清單
- [LLMQuant/awesome-trading-agents](https://github.com/LLMQuant/awesome-trading-agents)
  ——LLM交易agent、MCP server清單
- [AI4Finance-Foundation/FinRL](https://github.com/AI4Finance-Foundation/FinRL)
  ——強化學習做交易,路線跟AI-prompt判斷不同,較偏學術/需要訓練,參考價值較低

## 台股專屬

- [FinLab](https://ai.finlab.tw/)——台股資料庫+回測引擎,三行程式碼可測選股
  策略,有[公開策略庫](https://ai.finlab.tw/strategies/)可看別人已驗證過的
  台股選股邏輯
- [hu0937/FinPilot](https://github.com/hu0937/FinPilot)——用FinLab資料做的
  台股+美股量化分析平台,含自動策略探索、多空/盤整市場判斷、Kelly公式部位
  大小建議——部位大小建議的做法對「AI自己決定cash_ratio」這塊可能有參考價值

## 幣圈交易bot/策略

- [freqtrade/freqtrade](https://github.com/freqtrade/freqtrade)(2.5萬+ star)
  + [freqtrade-strategies](https://github.com/freqtrade/freqtrade-strategies)
  ——免費策略庫,可看裡面的技術指標邏輯
- [botcrypto-io/awesome-crypto-trading-bots](https://github.com/botcrypto-io/awesome-crypto-trading-bots)
  ——幣圈交易bot總覽清單

## 通用量化交易總覽

- [paperswithbacktest/awesome-systematic-trading](https://github.com/paperswithbacktest/awesome-systematic-trading)
  ——97個函式庫+40個策略的總覽,質量較高
- [je-suis-tm/quant-trading](https://github.com/je-suis-tm/quant-trading)
  ——RSI/布林通道/拋物線SAR等經典策略的Python實作範例
- [kernc/backtesting.py](https://github.com/kernc/backtesting.py)——輕量回測框架
