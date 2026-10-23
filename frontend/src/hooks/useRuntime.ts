import { useEffect, useState } from 'react';
import { api } from '../services/api';

export function useRuntime() {
  const [mode, setMode] = useState<'mock' | 'real' | 'unknown'>('unknown');
  useEffect(() => {
    api<{ ssh_mode: 'mock' | 'real' }>('/health').then(data => setMode(data.ssh_mode)).catch(() => setMode('unknown'));
  }, []);
  return mode;
}
