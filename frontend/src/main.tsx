import React, { useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import './style.css'

type Paper = {
  id: string; title: string; doi: string | null; pmid: string | null; pmcid: string | null
  authors: string[]; year: number | null; journal: string | null; abstract: string | null
  landing_url: string | null; first_source: string; license: string | null
}
type Reference = {
  id: string; ordinal: number; raw_text: string; doi: string | null; status: string
  confidence: number | null; match_source: string | null; paper: Paper | null
}
type Document = {
  id: string; filename: string; status: string; error: string | null
  sha256: string; references: Reference[]
}
type Download = {
  id: string; paper_id: string; paper_title: string | null; status: string
  source_url: string | null; license: string | null; sha256: string | null
  byte_size: number | null; error: string | null
}
type SearchResponse = { id: string; papers: Paper[]; errors?: Record<string, string> }
type SearchSummary = { id: string; query: string; created_at: string }
type DocumentSummary = { id: string; filename: string; status: string; created_at: string }

const SOURCES = [
  ['pubmed', 'PubMed'], ['europepmc', 'Europe PMC'],
  ['openalex', 'OpenAlex'], ['crossref', 'Crossref'],
] as const

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch('/api' + path, init)
  if (!response.ok) {
    const payload = await response.json().catch(() => null)
    const detail = payload?.detail
    throw new Error(typeof detail === 'string' ? detail : `请求失败 (${response.status})`)
  }
  return response.json() as Promise<T>
}

function App() {
  const [query, setQuery] = useState('')
  const [sources, setSources] = useState<string[]>(['pubmed', 'europepmc', 'openalex'])
  const [papers, setPapers] = useState<Paper[]>([])
  const [selected, setSelected] = useState<string[]>([])
  const [document, setDocument] = useState<Document | null>(null)
  const [downloads, setDownloads] = useState<Download[]>([])
  const [searches, setSearches] = useState<SearchSummary[]>([])
  const [documents, setDocuments] = useState<DocumentSummary[]>([])
  const [busy, setBusy] = useState('')
  const [message, setMessage] = useState('')

  async function refreshHistory() {
    const [searchResult, documentResult] = await Promise.all([
      api<{ searches: SearchSummary[] }>('/searches'),
      api<{ documents: DocumentSummary[] }>('/documents'),
    ])
    setSearches(searchResult.searches)
    setDocuments(documentResult.documents)
  }

  async function refreshDownloads() {
    const result = await api<{ downloads: Download[] }>('/downloads')
    setDownloads(result.downloads)
  }

  useEffect(() => {
    refreshDownloads().catch(() => {})
    refreshHistory().catch(() => {})
  }, [])

  useEffect(() => {
    if (!document || !['queued', 'processing'].includes(document.status)) return
    const timer = window.setInterval(() => {
      api<Document>(`/documents/${document.id}`).then(setDocument).catch(() => {})
    }, 2500)
    return () => window.clearInterval(timer)
  }, [document?.id, document?.status])

  useEffect(() => {
    if (!downloads.some(item => ['queued', 'processing'].includes(item.status))) return
    const timer = window.setInterval(() => refreshDownloads().catch(() => {}), 3000)
    return () => window.clearInterval(timer)
  }, [downloads])

  async function runSearch(event: React.FormEvent) {
    event.preventDefault()
    if (!query.trim() || !sources.length) return
    setBusy('search')
    setMessage('')
    try {
      const result = await api<SearchResponse>('/searches', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: query.trim(), sources, limit_per_source: 20 }),
      })
      setPapers(result.papers)
      setSelected([])
      refreshHistory().catch(() => {})
      const failures = Object.keys(result.errors || {})
      setMessage(`找到 ${result.papers.length} 篇文献。${failures.length ? ' 部分来源暂不可用：' + failures.join('、') : ''}`)
    } catch (error) { setMessage(String(error)) }
    finally { setBusy('') }
  }

  async function upload(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    if (!file) return
    setBusy('upload')
    setMessage('')
    try {
      const body = new FormData()
      body.append('file', file)
      const result = await api<{ id: string }>('/documents', { method: 'POST', body })
      setDocument(await api<Document>(`/documents/${result.id}`))
      refreshHistory().catch(() => {})
      setMessage('PDF 已上传，正在提取并匹配参考文献。')
    } catch (error) { setMessage(String(error)) }
    finally { setBusy(''); event.target.value = '' }
  }

  async function queueDownloads(ids: string[]) {
    if (!ids.length) return
    setBusy('download')
    setMessage('')
    try {
      const result = await api<{ downloads: Download[] }>('/downloads', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ paper_ids: ids }),
      })
      setDownloads(current => [...result.downloads, ...current.filter(old => !result.downloads.some(item => item.id === old.id))])
      setMessage(`已提交 ${result.downloads.length} 个开放获取全文任务。`)
    } catch (error) { setMessage(String(error)) }
    finally { setBusy('') }
  }

  async function matchReference(referenceId: string, paperId: string) {
    if (!document || !paperId) return
    setBusy(referenceId)
    try {
      await api(`/references/${referenceId}/match`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ paper_id: paperId }),
      })
      setDocument(await api<Document>(`/documents/${document.id}`))
    } catch (error) { setMessage(String(error)) }
    finally { setBusy('') }
  }

  function toggle(id: string) {
    setSelected(current => current.includes(id) ? current.filter(value => value !== id) : [...current, id])
  }

  const matched = [...new Set((document?.references || [])
    .filter(ref => ref.status === 'matched' && ref.paper?.doi)
    .map(ref => ref.paper!.id))]

  return <main>
    <header className="hero">
      <div className="eyebrow">MEDICAL LITERATURE WORKBENCH</div>
      <h1>医学文献检索与参考文献爬虫</h1>
      <p>检索医学文献，核对 PDF 引文，并批量获取有明确许可的开放获取全文。</p>
    </header>
    {message && <p className="notice" role="status">{message}</p>}

    <section className="panel" aria-labelledby="search-heading">
      <div className="section-top"><span className="step">01</span><h2 id="search-heading">检索文献</h2></div>
      <form onSubmit={runSearch}>
        <label htmlFor="query">主题、标题、作者或 DOI</label>
        <div className="search-row">
          <input id="query" value={query} onChange={e => setQuery(e.target.value)}
            placeholder="例如：influenza vaccine effectiveness older adults" minLength={3} maxLength={500} required />
          <button disabled={busy === 'search' || !sources.length}>{busy === 'search' ? '检索中…' : '开始检索'}</button>
        </div>
        <fieldset><legend>数据源</legend>
          {SOURCES.map(([key, label]) => <label className="check" key={key}>
            <input type="checkbox" checked={sources.includes(key)}
              onChange={() => setSources(current => current.includes(key) ? current.filter(x => x !== key) : [...current, key])} />
            {label}
          </label>)}
        </fieldset>
      </form>
      {searches.length > 0 && <label className="history">历史检索
        <select value="" onChange={async e => {
          if (!e.target.value) return
          try {
            const result = await api<SearchResponse>(`/searches/${e.target.value}`)
            setQuery(searches.find(item => item.id === e.target.value)?.query || '')
            setPapers(result.papers)
            setSelected([])
          } catch (error) { setMessage(String(error)) }
        }}>
          <option value="">选择历史检索</option>
          {searches.map(item => <option key={item.id} value={item.id}>{item.query} · {new Date(item.created_at).toLocaleString()}</option>)}
        </select>
      </label>}
      {!!papers.length && <>
        <div className="results-bar"><strong>{papers.length} 篇结果</strong>
          <button className="secondary" disabled={!selected.length || !!busy}
            onClick={() => queueDownloads(selected)}>下载已选 OA 全文（{selected.length}）</button>
        </div>
        <div className="paper-list">{papers.map(paper => <article className="paper" key={paper.id}>
          <label className="paper-select">
            <input type="checkbox" checked={selected.includes(paper.id)} disabled={!paper.doi}
              onChange={() => toggle(paper.id)} aria-label={`选择 ${paper.title}`} />
          </label>
          <div>
            <h3>{paper.landing_url ? <a href={paper.landing_url} target="_blank" rel="noreferrer">{paper.title}</a> : paper.title}</h3>
            <p>{paper.authors.slice(0, 3).join('、')}{paper.authors.length > 3 ? ' 等' : ''} · {paper.year || '年份待核'} · {paper.journal || paper.first_source}</p>
            <div className="chips"><span>{paper.first_source}</span>{paper.pmid && <span>PMID {paper.pmid}</span>}
              {paper.doi && <span>DOI {paper.doi}</span>}{paper.license && <span>许可 {paper.license}</span>}</div>
          </div>
        </article>)}</div>
      </>}
    </section>

    <section className="panel" aria-labelledby="references-heading">
      <div className="section-top"><span className="step">02</span><h2 id="references-heading">从 PDF 查找参考文献</h2></div>
      <p className="hint">上传可复制文字的 PDF，系统提取带编号的参考文献并尝试匹配 DOI。低置信度结果须先人工确认。</p>
      <label className="upload">选择 PDF（最大 25 MB）
        <input type="file" accept="application/pdf,.pdf" onChange={upload} disabled={!!busy} />
      </label>
      {documents.length > 0 && <label className="history">历史 PDF
        <select value="" onChange={async e => {
          if (!e.target.value) return
          try { setDocument(await api<Document>(`/documents/${e.target.value}`)) }
          catch (error) { setMessage(String(error)) }
        }}>
          <option value="">选择历史 PDF</option>
          {documents.map(item => <option key={item.id} value={item.id}>{item.filename} · {item.status}</option>)}
        </select>
      </label>}
      {document && <div className="document">
        <div className="results-bar"><strong>{document.filename}</strong><span className="status">{document.status}</span></div>
        {document.error && <p className="error">{document.error}</p>}
        {document.references.length > 0 && <>
          <div className="results-bar"><span>{document.references.length} 条参考文献，{matched.length} 篇已确认匹配</span>
            <button className="secondary" disabled={!matched.length || !!busy} onClick={() => queueDownloads(matched)}>
              批量下载已确认 OA 全文
            </button>
          </div>
          <ol className="refs">{document.references.map(ref => <li key={ref.id}>
            <div className="ref-heading"><span className={`badge ${ref.status}`}>{ref.status === 'matched' ? '已匹配' : ref.status === 'review' ? '待复核' : '未匹配'}</span>
              {ref.confidence !== null && <small>匹配分数 {Math.round(ref.confidence * 100)}%</small>}</div>
            <p>{ref.raw_text}</p>
            {ref.paper && <p className="candidate">候选：{ref.paper.title} {ref.paper.doi && `· DOI ${ref.paper.doi}`}</p>}
            {ref.status === 'review' && ref.paper && <button className="small secondary" disabled={!!busy}
              onClick={() => matchReference(ref.id, ref.paper!.id)}>确认此匹配</button>}
            {ref.status !== 'matched' && papers.length > 0 && <div className="manual">
              <label htmlFor={`match-${ref.id}`}>从上方检索结果中选择：</label>
              <select id={`match-${ref.id}`} defaultValue="" onChange={e => matchReference(ref.id, e.target.value)} disabled={!!busy}>
                <option value="">选择文献</option>
                {papers.filter(paper => paper.doi).map(paper => <option key={paper.id} value={paper.id}>{paper.title.slice(0, 100)}</option>)}
              </select>
            </div>}
          </li>)}</ol>
        </>}
      </div>}
    </section>

    <section className="panel" aria-labelledby="downloads-heading">
      <div className="section-top"><span className="step">03</span><h2 id="downloads-heading">下载记录</h2></div>
      <div className="results-bar"><p className="hint">只保存核实为 PDF 的开放获取文件，并记录来源、许可和 SHA-256。</p>
        <button className="text-button" onClick={() => refreshDownloads().catch(error => setMessage(String(error)))}>刷新</button></div>
      {downloads.length === 0 ? <p className="empty">暂无下载任务。</p> : <div className="downloads">
        {downloads.map(item => <div className="download" key={item.id}>
          <div><strong>{item.paper_title || item.paper_id}</strong>
            <p>{item.status === 'completed' ? `${item.license} · ${(item.byte_size! / 1024 / 1024).toFixed(1)} MB` : item.error || item.status}</p>
            {item.sha256 && <small>SHA-256 {item.sha256}</small>}
          </div>
          {item.status === 'completed' && <a className="button-link" href={`/api/downloads/${item.id}/file`}>保存 PDF</a>}
        </div>)}
      </div>}
    </section>
    <footer>本工具用于检索与核对；下载取决于各来源实际提供的开放获取许可。</footer>
  </main>
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>)
