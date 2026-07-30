# 履歷配對/修正 開源專案參考清單

2026-07-30 搜尋，為了評估「使用者上傳履歷 → 比對台灣實習頻道職缺 → AI給修改
建議」這類功能的前期調查。純參考清單，未包含任何程式碼依賴。

## 履歷↔職缺匹配

- [srbhr/Resume-Matcher](https://github.com/srbhr/Resume-Matcher) ——星數最高/
  最活躍，支援100+ LLM本地運行，計算ATS相容分數、自動改寫條列句提升匹配率
- [subhash-vadlamani/resume-job-description-analyzer](https://github.com/subhash-vadlamani/resume-job-description-analyzer)
  ——Python+spaCy/NLTK，抽取關鍵字+語意相似度評分
- [Prateek-27/TalentMatch](https://github.com/Prateek-27/TalentMatch)
  ——Flask+MongoDB的履歷職缺媒合平台
- [Fahad16301139/AI-Based-Resume-Classifier-and-Job-Matching-System](https://github.com/Fahad16301139/AI-Based-Resume-Classifier-and-Job-Matching-System)

## 履歷修正/ATS健檢

- [sunnypatell/ats-screener](https://github.com/sunnypatell/ats-screener) ——
  模擬6家真實企業ATS系統(Workday/Taleo/iCIMS/Greenhouse/Lever/SuccessFactors)
  如何解析履歷，純瀏覽器端跑，履歷不上傳伺服器(隱私性較好)
- [Hashsharma/ATS-Resume-Checker](https://github.com/Hashsharma/ATS-Resume-Checker)、
  [mayankkala/Advanced-ATS-Resume-Checker](https://github.com/mayankkala/Advanced-ATS-Resume-Checker)
  ——履歷+職缺說明輸入，給ATS分數+建議

## 中文/繁中專屬(跟台灣實習頻道的使用情境最貼近)

- [Jichengyuuuuu/resume-builder-skill](https://github.com/Jichengyuuuuu/resume-builder-skill)
  ——AI Agent Skill，模糊背景資訊生成專業中文履歷(HTML+DOCX)，支援ATS優化+
  職缺客製
- [Y1fe1-Yang/resume-assistant-skill](https://github.com/Y1fe1-Yang/resume-assistant-skill)
  ——5個專職agent分工(故事挖掘/職缺推薦/履歷優化/模擬面試/能力提升建議)，
  專為中文求職者設計
- [dyweb/awesome-resume-for-chinese](https://github.com/dyweb/awesome-resume-for-chinese)
  ——純模板收集(LaTeX/HTML)，無AI，適合中文字體排版
