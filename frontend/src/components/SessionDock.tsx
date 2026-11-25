import { Button, Dropdown, type MenuProps } from 'antd'
import {
  AuditOutlined, ControlOutlined, DesktopOutlined, FileSearchOutlined, LogoutOutlined,
  MenuOutlined, MobileOutlined, RiseOutlined, TeamOutlined,
} from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { useViewMode } from '../context/ViewModeContext'

export default function SessionDock() {
  const navigate = useNavigate()
  const { user, logout } = useAuth()
  const { effectiveMode, selectPreference } = useViewMode()
  const items: MenuProps['items'] = [
    { key: '/applicant', icon: <FileSearchOutlined />, label: '应聘者评估' },
    { key: '/hr', icon: <TeamOutlined />, label: 'HR 工作台' },
    { key: '/emerging', icon: <RiseOutlined />, label: '新兴岗位发现' },
    { key: '/review', icon: <AuditOutlined />, label: '岗位审核中心' },
    { key: '/admin', icon: <ControlOutlined />, label: '系统管理中心' },
    { type: 'divider' as const },
    { key: 'toggle-view', icon: <MobileOutlined />, label: '切换为手机端视图' },
    ...(user ? [
      { type: 'divider' as const },
      { key: 'logout', icon: <LogoutOutlined />, label: `退出 ${user.display_name}` },
    ] : []),
  ]
  const handleClick: MenuProps['onClick'] = ({ key }) => {
    if (key === 'logout') void logout().then(() => navigate('/'))
    else if (key === 'toggle-view') selectPreference('mobile')
    else navigate(key)
  }
  return (
    <div className="session-dock-cluster">
      <div className="view-mode-switch" role="group" aria-label="切换 PC 端或手机端视图">
        <button
          type="button"
          className={`view-mode-switch__btn ${effectiveMode === 'desktop' ? 'is-active' : ''}`}
          aria-pressed={effectiveMode === 'desktop'}
          onClick={() => selectPreference('desktop')}
        >
          <DesktopOutlined />
          <span>PC 端</span>
        </button>
        <button
          type="button"
          className={`view-mode-switch__btn ${effectiveMode === 'mobile' ? 'is-active' : ''}`}
          aria-pressed={effectiveMode === 'mobile'}
          onClick={() => selectPreference('mobile')}
        >
          <MobileOutlined />
          <span>手机端</span>
        </button>
      </div>
      <Dropdown menu={{ items, onClick: handleClick }} placement="bottomRight" trigger={['click']}>
        <Button className="session-dock" icon={<MenuOutlined />} aria-label="打开功能与账号菜单">
          {user ? user.display_name : '工作台'}
        </Button>
      </Dropdown>
    </div>
  )
}
