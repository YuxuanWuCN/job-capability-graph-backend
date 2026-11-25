import { useState, type ReactNode } from 'react'
import { Alert, Button, Input } from 'antd'
import {
  DownOutlined,
  LockOutlined,
  LoginOutlined,
  LogoutOutlined,
  ThunderboltOutlined,
  UpOutlined,
} from '@ant-design/icons'
import { useAuth } from '../context/AuthContext'
import type { AuthUser } from '../types/api'

type Role = AuthUser['role']

interface AuthGateProps {
  children: ReactNode
  roles?: Role[]
  title: string
  description: string
}

export default function AuthGate({ children, roles, title, description }: AuthGateProps) {
  const { user, loading, sessionError, login, loginAsGuest, logout } = useAuth()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [guestSubmitting, setGuestSubmitting] = useState(false)
  const [showAccountForm, setShowAccountForm] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const allowGuest = !roles || roles.includes('applicant')
  const accountFormVisible = !allowGuest || showAccountForm

  const handleGuest = async () => {
    if (guestSubmitting) return
    setGuestSubmitting(true)
    setError(null)
    try {
      await loginAsGuest()
    } catch (value) {
      const message = value instanceof Error ? value.message : '游客登录失败，请稍后重试'
      setError(message)
    } finally {
      setGuestSubmitting(false)
    }
  }

  const handleLogin = async () => {
    if (!username.trim() || password.length < 8 || submitting) return
    setSubmitting(true)
    setError(null)
    try {
      await login(username.trim(), password)
    } catch (value) {
      const message = value instanceof Error ? value.message : '登录失败，请检查账号与密码'
      setError(message)
    } finally {
      setSubmitting(false)
    }
  }

  if (loading) {
    return (
      <main className="workspace-page workspace-page--center" aria-busy="true">
        <div className="workspace-skeleton" aria-label="正在检查登录状态">
          <span /><span /><span />
        </div>
      </main>
    )
  }

  if (!user) {
    return (
      <main className="workspace-page workspace-page--center">
        <section className="auth-panel" aria-labelledby="auth-title">
          <LockOutlined className="auth-panel__icon" />
          <h1 id="auth-title">{title}</h1>
          <p>{description}</p>
          {allowGuest && (
            <>
              <div className="auth-panel__guest">
                <Button
                  type="primary"
                  size="large"
                  block
                  icon={<ThunderboltOutlined />}
                  loading={guestSubmitting}
                  onClick={() => void handleGuest()}
                >
                  本机免注册直接开始
                </Button>
                <p className="auth-panel__hint">
                  免注册、免手机号验证，进入后本机自动保留 7 天会话（简历仅当前设备可见）。
                </p>
              </div>
              <button
                type="button"
                className="auth-panel__toggle"
                aria-expanded={showAccountForm}
                onClick={() => setShowAccountForm((prev) => !prev)}
              >
                <span>管理员 / 已有账号登录</span>
                {showAccountForm ? <UpOutlined /> : <DownOutlined />}
              </button>
            </>
          )}
          {accountFormVisible && (
            <div className="auth-panel__form">
              <Input
                size="large"
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                placeholder="用户名"
                autoComplete="username"
                aria-label="用户名"
                onPressEnter={() => void handleLogin()}
              />
              <Input.Password
                size="large"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                placeholder="密码"
                autoComplete="current-password"
                aria-label="密码"
                onPressEnter={() => void handleLogin()}
              />
              <Button
                type={allowGuest ? 'default' : 'primary'}
                size="large"
                icon={<LoginOutlined />}
                loading={submitting}
                disabled={!username.trim() || password.length < 8}
                onClick={() => void handleLogin()}
              >
                登录并继续
              </Button>
            </div>
          )}
          {(error || sessionError) && <Alert type="error" showIcon message={error || sessionError} />}
          {!allowGuest && (
            <p className="auth-panel__hint">
              此工作台仅限授权账号访问，请使用管理员或 HR 账号登录。
            </p>
          )}
        </section>
      </main>
    )
  }

  // 管理员拥有全部工作台的访问权，不再按角色拦截
  if (roles && user.role !== 'admin' && !roles.includes(user.role)) {
    return (
      <main className="workspace-page workspace-page--center">
        <section className="auth-panel">
          <LockOutlined className="auth-panel__icon" />
          <h1>当前账号无权访问</h1>
          <p>此工作台需要 {roles.map(roleLabel).join('或')} 角色，当前登录为{roleLabel(user.role)}。</p>
          <Button size="large" block icon={<LogoutOutlined />} onClick={() => void logout()}>
            退出当前会话并切换账号
          </Button>
        </section>
      </main>
    )
  }

  return children
}

function roleLabel(role: Role) {
  if (role === 'admin') return '管理员'
  if (role === 'hr') return 'HR'
  return '应聘者'
}
