import { useState } from 'react'
import { ArrowRightOutlined, CheckOutlined } from '@ant-design/icons'
import { FrameCorners } from './FrameCorners'
import type { Planet, Star } from '../types/graph'

interface MobileGraph2DViewProps {
  stars: Star[]
  planets: Planet[]
  activeStar: Star | null
  selectedPlanet: Planet | null
  onSelectStar: (star: Star) => void
  onSelectPlanet: (planet: Planet) => void
  mode?: 'explorer' | 'embedded'
  matchedSkillNames?: string[]
  onOpenFullGraph?: () => void
}

const PLANET_TYPE_COLOR: Record<Planet['type'], string> = {
  core: '#ee1212',
  foundation: '#dad0c8',
  frontier: '#e4b592',
}

const PLANET_TYPE_LABEL: Record<Planet['type'], string> = {
  core: '核心技能',
  foundation: '基础技能',
  frontier: '前沿技能',
}

export function MobileGraph2DView({
  stars,
  planets,
  activeStar,
  selectedPlanet,
  onSelectStar,
  onSelectPlanet,
  mode = 'explorer',
  matchedSkillNames = [],
  onOpenFullGraph,
}: MobileGraph2DViewProps) {
  const [expandedStrip, setExpandedStrip] = useState(false)
  const starPlanets = activeStar
    ? planets.filter((planet) => planet.starId === activeStar.id)
    : []
  const innerPlanets = starPlanets.filter((planet) => planet.isRequired)
  const outerPlanets = starPlanets.filter((planet) => !planet.isRequired)

  const matchedSkillSet = new Set(
    matchedSkillNames.map((name) => name.trim().toLowerCase()).filter(Boolean),
  )

  const inspectedPlanet =
    mode === 'embedded' &&
    activeStar &&
    selectedPlanet &&
    selectedPlanet.starId === activeStar.id
      ? selectedPlanet
      : null

  const inspectedMatched = inspectedPlanet
    ? matchedSkillSet.has(inspectedPlanet.label.trim().toLowerCase())
    : false

  return (
    <div className={`mgraph2d ${mode === 'embedded' ? 'mgraph2d--embedded' : ''}`}>
      {mode === 'explorer' && stars.length === 0 && (
        <div className="mgraph2d__empty archive-panel glass">
          <FrameCorners />
          <strong>没有找到匹配的岗位</strong>
          <span>请清空搜索或切换筛选条件</span>
        </div>
      )}

      {mode === 'explorer' && stars.length > 0 && (
        <div className="mgraph2d__strip-wrap">
          <div className="mgraph2d__strip-head">
            <span>
              共 {stars.length} 个可选岗位
              {activeStar ? ` · 当前：${activeStar.label}` : ''}
            </span>
            {stars.length > 8 && (
              <button
                type="button"
                className="mgraph2d__strip-toggle"
                onClick={() => setExpandedStrip((value) => !value)}
              >
                {expandedStrip ? '收起网格' : '展开全部'}
              </button>
            )}
          </div>
          <div
            className={`mgraph2d__job-strip ${expandedStrip ? 'mgraph2d__job-strip--expanded' : ''}`}
            role="tablist"
            aria-label="切换岗位图谱"
          >
            {stars.map((star) => {
              const active = activeStar?.id === star.id
              const sampleTotal = star.jobCount ?? star.sources
              return (
                <button
                  key={star.id}
                  type="button"
                  role="tab"
                  aria-selected={active}
                  className={`mgraph2d__job-chip ${active ? 'is-active' : ''}`}
                  onClick={() => {
                    onSelectStar(star)
                    if (expandedStrip) setExpandedStrip(false)
                  }}
                >
                  <span className="mgraph2d__dot" style={{ background: star.color }} />
                  <strong>{star.label}</strong>
                  <em>{sampleTotal.toLocaleString('zh-CN')}</em>
                </button>
              )
            })}
          </div>
        </div>
      )}

      {activeStar && (
        <section className="mgraph2d__card archive-panel glass" aria-label={`${activeStar.label} 2D 能力轨道`}>
          <FrameCorners />
          <header className="mgraph2d__header">
            <div className="mgraph2d__meta-row">
              <div className="mgraph2d__domain">
                <span className="mgraph2d__dot" style={{ background: activeStar.color }} />
                <span>{activeStar.domain}</span>
                {activeStar.isEmerging ? (
                  <span className="mgraph2d__badge mgraph2d__badge--emerging">新兴候选</span>
                ) : (
                  <span className="mgraph2d__badge mgraph2d__badge--official">核心岗位</span>
                )}
              </div>
              <span className="mgraph2d__badge">2D 双环图谱</span>
            </div>

            <h2 style={{ color: activeStar.color }}>{activeStar.label}</h2>
            <p className="mgraph2d__subtitle">
              {mode === 'explorer'
                ? `基于 ${(activeStar.jobCount ?? activeStar.sources).toLocaleString('zh-CN')} 条 JD 提炼 · 点击任意技能胶囊反查关联岗位（如需 3D 星系可点顶部 [PC 端]）`
                : '点击技能胶囊查看轨道层级与简历覆盖状态'}
            </p>

            <div className="mgraph2d__metrics">
              <div>
                <span>JD 样本</span>
                <strong>{(activeStar.jobCount ?? activeStar.sources).toLocaleString('zh-CN')}</strong>
              </div>
              <div>
                <span>内环必备</span>
                <strong>{innerPlanets.length}</strong>
              </div>
              <div>
                <span>外环加分</span>
                <strong>{outerPlanets.length}</strong>
              </div>
              <div>
                <span>来源站点</span>
                <strong>{Object.keys(activeStar.sourceCounts ?? {}).length || 1}</strong>
              </div>
            </div>
          </header>

          <div className="mgraph2d__rings">
            <div className="mgraph2d__track mgraph2d__track--inner">
              <div className="mgraph2d__track-head">
                <div className="mgraph2d__track-title">
                  <span className="mgraph2d__ring-dot mgraph2d__ring-dot--inner" />
                  <strong>内环 · 必备能力</strong>
                  <em>{innerPlanets.length}</em>
                </div>
                <span className="mgraph2d__track-legend">红=核心硬技能 · 灰=通用基础</span>
              </div>

              {innerPlanets.length > 0 ? (
                <div className="mgraph2d__pills">
                  {innerPlanets.map((planet) => {
                    const selected = selectedPlanet?.id === planet.id
                    const matched =
                      mode === 'embedded' &&
                      matchedSkillSet.has(planet.label.trim().toLowerCase())
                    return (
                      <button
                        key={planet.id}
                        type="button"
                        className={`mgraph2d__pill ${selected ? 'is-selected' : ''} ${matched ? 'is-matched' : ''}`}
                        onClick={() => onSelectPlanet(planet)}
                      >
                        <span
                          className="mgraph2d__dot"
                          style={{ background: PLANET_TYPE_COLOR[planet.type] }}
                        />
                        <span className="mgraph2d__pill-label">{planet.label}</span>
                        <em className="mgraph2d__pill-meta">
                          {mode === 'embedded' ? (
                            matched ? (
                              <>
                                <CheckOutlined /> 已覆盖
                              </>
                            ) : (
                              '待补齐'
                            )
                          ) : (
                            `${Math.round(planet.confidence)}%`
                          )}
                        </em>
                      </button>
                    )
                  })}
                </div>
              ) : (
                <div className="mgraph2d__track-empty">当前筛选条件下暂无内环必备技能</div>
              )}
            </div>

            <div className="mgraph2d__track mgraph2d__track--outer">
              <div className="mgraph2d__track-head">
                <div className="mgraph2d__track-title">
                  <span className="mgraph2d__ring-dot mgraph2d__ring-dot--outer" />
                  <strong>外环 · 加分前沿</strong>
                  <em>{outerPlanets.length}</em>
                </div>
              </div>

              {outerPlanets.length > 0 ? (
                <div className="mgraph2d__pills">
                  {outerPlanets.map((planet) => {
                    const selected = selectedPlanet?.id === planet.id
                    const matched =
                      mode === 'embedded' &&
                      matchedSkillSet.has(planet.label.trim().toLowerCase())
                    return (
                      <button
                        key={planet.id}
                        type="button"
                        className={`mgraph2d__pill ${selected ? 'is-selected' : ''} ${matched ? 'is-matched' : ''}`}
                        onClick={() => onSelectPlanet(planet)}
                      >
                        <span
                          className="mgraph2d__dot"
                          style={{ background: PLANET_TYPE_COLOR[planet.type] }}
                        />
                        <span className="mgraph2d__pill-label">{planet.label}</span>
                        <em className="mgraph2d__pill-meta">
                          {mode === 'embedded' ? (
                            matched ? (
                              <>
                                <CheckOutlined /> 已覆盖
                              </>
                            ) : (
                              '待补齐'
                            )
                          ) : (
                            `${Math.round(planet.confidence)}%`
                          )}
                        </em>
                      </button>
                    )
                  })}
                </div>
              ) : (
                <div className="mgraph2d__track-empty">当前筛选条件下暂无外环加分技能</div>
              )}
            </div>
          </div>

          {inspectedPlanet && (
            <div className="mgraph2d__inspector">
              <div className="mgraph2d__inspector-head">
                <strong>{inspectedPlanet.label}</strong>
                <span>{PLANET_TYPE_LABEL[inspectedPlanet.type]}</span>
              </div>
              <div className="mgraph2d__inspector-meta">
                <span>{inspectedPlanet.isRequired ? '内环 · 必备能力' : '外环 · 加分能力'}</span>
                <span>AI 置信度 {Math.round(inspectedPlanet.confidence)}%</span>
              </div>
              <p className="mgraph2d__inspector-status">
                {inspectedMatched
                  ? '简历已覆盖该能力，可优先在面试作品集中展示相关证据。'
                  : '当前简历未覆盖，建议纳入成长路径进行针对性补齐。'}
              </p>
              {onOpenFullGraph && (
                <button
                  type="button"
                  className="btn btn-sm btn-ghost mgraph2d__inspector-btn"
                  onClick={onOpenFullGraph}
                >
                  去完整能力星图反查关联岗位
                  <ArrowRightOutlined />
                </button>
              )}
            </div>
          )}

          {mode === 'explorer' && activeStar.sampleJobs && activeStar.sampleJobs.length > 0 && (
            <div className="mgraph2d__samples">
              <div className="mgraph2d__samples-title">样例岗位（近期真实 JD）</div>
              <div className="graph-sample-list">
                {activeStar.sampleJobs.slice(0, 4).map((job) => (
                  <a
                    key={`${job.url || job.jobName}-${job.companyName}`}
                    className="archive-row glass"
                    href={job.url || undefined}
                    target={job.url ? '_blank' : undefined}
                    rel={job.url ? 'noreferrer' : undefined}
                  >
                    <div
                      className="graph-sample-list__dot"
                      style={{ background: activeStar.color }}
                    />
                    <div>
                      <strong>{job.jobName}</strong>
                      <span>
                        {job.companyName} · {job.city} · {job.salary || '薪资未标注'}
                      </span>
                    </div>
                    <em>{job.source}</em>
                  </a>
                ))}
              </div>
            </div>
          )}
        </section>
      )}
    </div>
  )
}
