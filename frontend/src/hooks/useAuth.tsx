import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import { api, setCSRF } from '../services/api';
import type { Auth, User } from '../types';

interface AuthContextType {
  user: User | null; loading: boolean; error: string;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>; clear: () => void;
}
const AuthContext = createContext<AuthContextType>(null!);
export const useAuth = () => useContext(AuthContext);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const accept = (auth: Auth) => { setCSRF(auth.csrf_token); setUser(auth.user); setError(''); };
  const clear = () => { setCSRF(''); setUser(null); };
  useEffect(() => {
    api<Auth>('/auth/me').then(accept).catch(err => {
      if (err.status !== 401) setError('无法连接后端，请确认服务已启动后刷新页面');
    }).finally(() => setLoading(false));
    window.addEventListener('session-expired', clear);
    return () => window.removeEventListener('session-expired', clear);
  }, []);
  const login = async (username: string, password: string) => accept(await api<Auth>('/auth/login', 'POST', { username, password }));
  const logout = async () => { await api('/auth/logout', 'POST'); clear(); };
  return <AuthContext.Provider value={{ user, loading, error, login, logout, clear }}>{children}</AuthContext.Provider>;
}
