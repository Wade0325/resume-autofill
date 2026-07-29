// 主題分類。我的資料與匯入履歷共用同一套，使用者在兩頁看到的分類才一致。
// prefix 對應 backend/core/schema.py 的欄位命名。

export type Section = {
  id: string
  title: string
  prefix: string
  repeatRoot?: string // 有值代表這一區是可增減的多筆資料
}

export const SECTIONS: Section[] = [
  { id: 'basic', title: '基本資料', prefix: 'basic.' },
  { id: 'contact', title: '聯絡方式', prefix: 'contact.' },
  { id: 'job', title: '應徵資訊', prefix: 'job.' },
  { id: 'education', title: '學歷', prefix: 'education[].', repeatRoot: 'education' },
  { id: 'experience', title: '工作經歷', prefix: 'experience[].', repeatRoot: 'experience' },
  { id: 'skills', title: '專長技能', prefix: 'skills.' },
  { id: 'family', title: '家庭狀況', prefix: 'family[].', repeatRoot: 'family' },
  { id: 'reference', title: '任用諮詢人', prefix: 'reference[].', repeatRoot: 'reference' },
  { id: 'emergency', title: '緊急聯絡人', prefix: 'emergency.' },
  { id: 'declaration', title: '聲明事項', prefix: 'declaration.' },
  { id: 'autobiography', title: '自傳', prefix: 'autobiography' },
]
