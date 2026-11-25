import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import type { AuthUser } from '../types/api';
import { API_BASE_URL } from '../services/apiBase';

interface AuthContextValue {
  user: AuthUser | null;
  loading: boolean;
  sessionError: string | null;
  ensureSession: () => Promise<AuthUser | null>;
  login: (username: string, password: string) => Promise<void>;
  loginAsGuest: () => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

async function fetchMe(): Promise<AuthUser | null> {
  const res = await fetch(`${API_BASE_URL}/api/v1/auth/me`, {
    credentials: 'include',
  });
  if (res.status === 401) return null;
  if (!res.ok) throw new Error('无法读取登录状态');
  const body = await res.json();
  return (body.data ?? body) as AuthUser;
}

/**
 * 游客模式：由用户在登录页显式点击「以游客身份进入」时才签发，
 * 不再自动登录，避免一进站就被当成游客、也避免挡住管理员登录。
 */
async function createGuestSession(): Promise<AuthUser | null> {
  const res = await fetch(`${API_BASE_URL}/api/v1/auth/guest`, {
    method: 'POST',
    credentials: 'include',
  });
  if (!res.ok) return null;
  const body = await res.json();
  return (body.data ?? body) as AuthUser;
}

export const AuthProvider = ({ children }: { children: ReactNode }) => {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [sessionError, setSessionError] = useState<string | null>(null);
  const sessionPromiseRef = useRef<Promise<AuthUser | null> | null>(null);
  const bootstrappedRef = useRef(false);

  const ensureSession = useCallback(async (): Promise<AuthUser | null> => {
    if (user) return user;
    if (sessionPromiseRef.current) return sessionPromiseRef.current;

    const request = (async () => {
      setLoading(true);
      setSessionError(null);
      try {
        const current = await fetchMe();
        if (current) {
          setUser(current);
          return current;
        }
        setUser(null);
        return null;
      } catch {
        setUser(null);
        setSessionError('无法连接后端服务，请检查 API 是否已启动');
        return null;
      } finally {
        setLoading(false);
      }
    })();

    sessionPromiseRef.current = request;
    void request.then(
      () => {
        if (sessionPromiseRef.current === request) sessionPromiseRef.current = null;
      },
      () => {
        if (sessionPromiseRef.current === request) sessionPromiseRef.current = null;
      },
    );
    return request;
  }, [user]);

  useEffect(() => {
    if (bootstrappedRef.current) return;
    bootstrappedRef.current = true;
    void ensureSession();
  }, [ensureSession]);

  const login = async (username: string, password: string) => {
    const res = await fetch(`${API_BASE_URL}/api/v1/auth/login`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error((body as any).error?.message ?? (body as any).detail ?? '登录失败');
    }
    const body = await res.json();
    setUser((body.data ?? body) as AuthUser);
    setSessionError(null);
  };

  const loginAsGuest = async () => {
    const guest = await createGuestSession();
    if (!guest) throw new Error('游客登录失败，请稍后重试');
    setUser(guest);
    setSessionError(null);
  };

  const logout = async () => {
    const csrf = document.cookie.match(/(?:^|; )csrf=([^;]*)/)?.[1];
    await fetch(`${API_BASE_URL}/api/v1/auth/logout`, {
      method: 'POST',
      credentials: 'include',
      headers: csrf ? { 'X-CSRF-Token': decodeURIComponent(csrf) } : {},
    }).catch(() => {});
    setUser(null);
    sessionPromiseRef.current = null;
  };

  return (
    <AuthContext.Provider
      value={{ user, loading, sessionError, ensureSession, login, loginAsGuest, logout }}
    >
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = () => {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
};
