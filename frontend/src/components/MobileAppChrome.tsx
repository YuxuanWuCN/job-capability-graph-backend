import { useEffect, useState } from 'react'
import {
  AppstoreOutlined,
  AuditOutlined,
  CloseOutlined,
  ControlOutlined,
  DesktopOutlined,
  FileSearchOutlined,
  HomeOutlined,
  LeftOutlined,
  LogoutOutlined,
  MobileOutlined,
  NodeIndexOutlined,
  RiseOutlined,
  TeamOutlined,
  UserOutlined,
} from '@ant-design/icons'
import { useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { useViewMode } from '../context/ViewModeContext'
import type { AuthUser } from '../types/api'

const PAGE_TITLES: Record<string, string> = {
  '/': '岗智融析',
  '/applicant': '简历评估',
  '/resume-match': '简历评估',
  '/graph': '能力星图',
  '/hr': 'HR 工作台',
  '/hr-match': 'HR 工作台',
  '/emerging': '新兴岗位',
  '/new-jobs': '新兴岗位',
  '/review': '审核中心',
  '/admin': '系统管理',
  '/register': '学生注册',
}

const WORKSPACE_ROUTES = [
  {
    path: '/hr',
    title: 'HR 招聘工作台',
    subtitle: 'JD 解析、要求确认与候选人匹配快照',
    icon: <TeamOutlined />,
  },
  {
    path: '/emerging',
    title: '新兴岗位发现',
    subtitle: '市场 JD 聚类与候选技能组合洞察',
    icon: <RiseOutlined />,
  },
  {
    path: '/review',
    title: '岗位审核中心',
    subtitle: '标准能力提案审批与版本发布',
    icon: <AuditOutlined />,
  },
  {
    path: '/admin',
    title: '系统管理中心',
    subtitle: '账号管理、任务追踪与依赖监控',
    icon: <ControlOutlined />,
  },
] as const

const MORE_PATHS = new Set(['/hr', '/hr-match', '/emerging', '/new-jobs', '/review', '/admin'])

function roleLabel(role: AuthUser['role']) {
  if (role === 'admin') return '管理员'
  if (role === 'hr') return 'HR'
  return '学生/游客'
}

export default function MobileAppChrome() {
  const location = useLocation()
  const navigate = useNavigate()
  const { user, logout } = useAuth()
  const { preference, effectiveMode, showDevicePrompt, selectPreference, dismissPrompt } = useViewMode()
  const [sheetOpen, setSheetOpen] = useState(false)

  const { pathname } = location
  const isHome = pathname === '/'
  const isApplicant = pathname === '/applicant' || pathname === '/resume-match'
  const isGraph = pathname === '/graph'
  const isMoreActive = sheetOpen || MORE_PATHS.has(pathname)
  const pageTitle = PAGE_TITLES[pathname] ?? '岗智融析'

  useEffect(() => {
    setSheetOpen(false)
  }, [pathname])

  useEffect(() => {
    if (!sheetOpen) return
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setSheetOpen(false)
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [sheetOpen])

  const openRoute = (path: string) => {
    setSheetOpen(false)
    navigate(path)
  }

  return (
    <>
      <header className="mobile-topbar">
        <div className="mobile-topbar__left">
          {!isHome && (
            <button
              type="button"
              className="mobile-topbar__back"
              onClick={() => navigate('/')}
              aria-label="返回月面档案首页"
            >
              <LeftOutlined />
            </button>
          )}
          <div className="mobile-topbar__brand">
            <span className="mobile-topbar__kicker">ARCHIVE HUD</span>
            <strong className="mobile-topbar__title">{pageTitle}</strong>
          </div>
        </div>
        <div className="mobile-topbar__actions">
          <button
            type="button"
            className="mobile-topbar__mode"
            onClick={() => selectPreference('desktop')}
            aria-label="切换为 PC 端视图"
          >
            <DesktopOutlined />
            <span>PC 端</span>
          </button>
          <button
            type="button"
            className="mobile-topbar__user"
            onClick={() => setSheetOpen((open) => !open)}
            aria-expanded={sheetOpen}
            aria-label="打开工作台与账号菜单"
          >
            <UserOutlined />
            <span>{user ? user.display_name : '工作台'}</span>
          </button>
        </div>
      </header>

      <nav className="mobile-bottom-nav" role="navigation" aria-label="移动端主导航">
        <button
          type="button"
          className={`mobile-bottom-nav__item ${isHome && !sheetOpen ? 'is-active' : ''}`}
          onClick={() => openRoute('/')}
        >
          <HomeOutlined />
          <span>首页</span>
        </button>
        <button
          type="button"
          className={`mobile-bottom-nav__item ${isApplicant && !sheetOpen ? 'is-active' : ''}`}
          onClick={() => openRoute('/applicant')}
        >
          <FileSearchOutlined />
          <span>简历评估</span>
        </button>
        <button
          type="button"
          className={`mobile-bottom-nav__item ${isGraph && !sheetOpen ? 'is-active' : ''}`}
          onClick={() => openRoute('/graph')}
        >
          <NodeIndexOutlined />
          <span>能力星图</span>
        </button>
        <button
          type="button"
          className={`mobile-bottom-nav__item ${isMoreActive ? 'is-active' : ''}`}
          onClick={() => setSheetOpen((open) => !open)}
          aria-expanded={sheetOpen}
        >
          <AppstoreOutlined />
          <span>更多</span>
        </button>
      </nav>

      {sheetOpen && (
        <>
          <div
            className="mobile-sheet-backdrop"
            onClick={() => setSheetOpen(false)}
            aria-hidden="true"
          />
          <section
            className="mobile-sheet"
            role="dialog"
            aria-modal="true"
            aria-label="工作台与会话菜单"
          >
            <div className="mobile-sheet__head">
              <div>
                <span className="mobile-sheet__kicker">WORKSPACE / MENU</span>
                <strong className="mobile-sheet__session">
                  {user ? `${user.display_name}（${roleLabel(user.role)}）` : '未登录 / 游客可直接评估'}
                </strong>
              </div>
              <button
                type="button"
                className="mobile-sheet__close"
                onClick={() => setSheetOpen(false)}
                aria-label="关闭工作台菜单"
              >
                <CloseOutlined />
              </button>
            </div>

            <div className="mobile-sheet__modes" role="group" aria-label="浏览模式选择">
              <span>浏览模式</span>
              <div className="mobile-sheet__mode-row">
                <button
                  type="button"
                  className={preference === 'auto' ? 'is-active' : ''}
                  onClick={() => selectPreference('auto')}
                >
                  跟随设备
                </button>
                <button
                  type="button"
                  className={preference === 'mobile' ? 'is-active' : ''}
                  onClick={() => selectPreference('mobile')}
                >
                  <MobileOutlined /> 手机端
                </button>
                <button
                  type="button"
                  className={preference === 'desktop' ? 'is-active' : ''}
                  onClick={() => selectPreference('desktop')}
                >
                  <DesktopOutlined /> PC 端
                </button>
              </div>
            </div>

            <div className="mobile-sheet__list">
              {WORKSPACE_ROUTES.map((item) => {
                const active = pathname === item.path || (item.path === '/hr' && pathname === '/hr-match') || (item.path === '/emerging' && pathname === '/new-jobs')
                return (
                  <button
                    key={item.path}
                    type="button"
                    className={`mobile-sheet__row ${active ? 'is-active' : ''}`}
                    onClick={() => openRoute(item.path)}
                  >
                    <span className="mobile-sheet__icon">{item.icon}</span>
                    <span className="mobile-sheet__copy">
                      <strong>{item.title}</strong>
                      <small>{item.subtitle}</small>
                    </span>
                  </button>
                )
              })}
            </div>

            {user && (
              <div className="mobile-sheet__footer">
                <button
                  type="button"
                  className="mobile-sheet__logout"
                  onClick={() => void logout().then(() => { setSheetOpen(false); navigate('/') })}
                >
                  <LogoutOutlined />
                  <span>退出当前会话</span>
                </button>
              </div>
            )}
          </section>
        </>
      )}

      {showDevicePrompt && (
        <aside className="device-mode-banner" role="region" aria-label="选择访问终端">
          <div className="device-mode-banner__copy">
            <span>VIEW MODE</span>
            <p>
              当前已自动识别为{effectiveMode === 'mobile' ? '手机端' : 'PC 端'}，可手动切换：
            </p>
          </div>
          <div className="device-mode-banner__actions">
            <button
              type="button"
              className={`device-mode-banner__btn ${effectiveMode === 'desktop' ? 'is-primary' : ''}`}
              onClick={() => selectPreference('desktop')}
            >
              <DesktopOutlined /> PC 端
            </button>
            <button
              type="button"
              className={`device-mode-banner__btn ${effectiveMode === 'mobile' ? 'is-primary' : ''}`}
              onClick={() => selectPreference('mobile')}
            >
              <MobileOutlined /> 手机端
            </button>
            <button
              type="button"
              className="device-mode-banner__close"
              onClick={dismissPrompt}
              aria-label="关闭终端切换提示"
            >
              <CloseOutlined />
            </button>
          </div>
        </aside>
      )}
    </>
  )
}
