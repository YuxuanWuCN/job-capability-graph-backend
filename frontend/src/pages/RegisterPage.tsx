import { useState } from 'react'
import { Alert, Button, Input } from 'antd'
import { UserAddOutlined } from '@ant-design/icons'
import { useSearchParams } from 'react-router-dom'
import { API_BASE_URL } from '../services/apiBase'

const ERROR_MESSAGES: Record<string, string> = {
  REGISTRATION_DISABLED: '当前未开放学生注册，请联系组织者。',
  INVITE_CODE_INVALID: '邀请码不正确，请向组织者确认。',
  USERNAME_ALREADY_EXISTS: '该用户名已被使用，请换一个。',
  REGISTRATION_RATE_LIMITED: '失败次数过多，请稍后再试。',
}

export default function RegisterPage() {
  const [searchParams] = useSearchParams()
  const [inviteCode, setInviteCode] = useState(searchParams.get('code') ?? '')
  const [username, setUsername] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const trimmedUsername = username.trim()
  const trimmedDisplayName = displayName.trim()
  const passwordsMatch = password === confirmPassword
  const canSubmit =
    inviteCode.trim().length > 0 &&
    trimmedUsername.length >= 3 &&
    trimmedUsername.length <= 64 &&
    trimmedDisplayName.length >= 1 &&
    trimmedDisplayName.length <= 100 &&
    password.length >= 8 &&
    password.length <= 128 &&
    passwordsMatch

  const handleSubmit = async () => {
    if (!canSubmit || submitting) return
    setSubmitting(true)
    setError(null)
    try {
      const res = await fetch(`${API_BASE_URL}/api/v1/auth/register`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          username: trimmedUsername,
          display_name: trimmedDisplayName,
          password,
          invite_code: inviteCode.trim(),
        }),
      })
      if (!res.ok) {
        const body: { error?: { code?: string } } = await res.json().catch(() => ({}))
        setError(ERROR_MESSAGES[body.error?.code ?? ''] ?? '注册失败，请检查填写内容后重试。')
        return
      }
      window.location.assign('/applicant')
    } catch {
      setError('无法连接服务，请稍后再试。')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="workspace-page workspace-page--center">
      <section className="auth-panel" aria-labelledby="register-title">
        <UserAddOutlined className="auth-panel__icon" />
        <h1 id="register-title">学生注册</h1>
        <p>使用组织者提供的邀请码创建账号，注册后即可上传简历、查看能力画像与岗位推荐。</p>
        <div className="auth-panel__form">
          <Input
            size="large"
            value={inviteCode}
            onChange={(event) => setInviteCode(event.target.value)}
            placeholder="邀请码"
            aria-label="邀请码"
            autoComplete="off"
          />
          <Input
            size="large"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            placeholder="用户名（3–64 个字符，用于今后登录）"
            aria-label="用户名"
            autoComplete="username"
          />
          <Input
            size="large"
            value={displayName}
            onChange={(event) => setDisplayName(event.target.value)}
            placeholder="姓名或昵称"
            aria-label="姓名或昵称"
            autoComplete="name"
          />
          <Input.Password
            size="large"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            placeholder="密码（至少 8 位）"
            aria-label="密码"
            autoComplete="new-password"
          />
          <Input.Password
            size="large"
            value={confirmPassword}
            onChange={(event) => setConfirmPassword(event.target.value)}
            placeholder="再次输入密码"
            aria-label="确认密码"
            autoComplete="new-password"
            onPressEnter={() => void handleSubmit()}
          />
          {confirmPassword.length > 0 && !passwordsMatch && (
            <Alert type="warning" showIcon message="两次输入的密码不一致" />
          )}
          <Button
            type="primary"
            size="large"
            icon={<UserAddOutlined />}
            loading={submitting}
            disabled={!canSubmit}
            onClick={() => void handleSubmit()}
          >
            注册并进入
          </Button>
        </div>
        {error && <Alert type="error" showIcon message={error} />}
        <p>
          <a href="/applicant">已有账号？前往简历能力评估页登录</a>
        </p>
      </section>
    </main>
  )
}
