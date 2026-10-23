export interface User {
  id: number;
  username: string;
  role: 'admin' | 'user';
  official_account_id: number | null;
  remote_home: string | null;
  status: 'active' | 'disabled';
  created_at: string;
  updated_at: string;
  last_login_at: string | null;
}
export interface Auth { user: User; csrf_token: string }
export interface OfficialAccount {
  id: number;
  alias: string;
  ssh_host: string;
  ssh_port: number;
  ssh_username: string;
  ssh_key_reference: string;
  enabled: boolean;
  created_at: string;
}
export type AccountInput = Omit<OfficialAccount, 'id' | 'created_at'>;
export interface Probe { mode: string; connected: boolean; home: string; shell?: string; sftp: boolean; all_passed?: boolean }
