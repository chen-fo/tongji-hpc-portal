import ReactDOM from 'react-dom/client';
import { ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { AuthProvider } from './hooks/useAuth';
import App from './App';
import './styles.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <ConfigProvider locale={zhCN} theme={{ token: { colorPrimary: '#176b70', borderRadius: 8, fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif' } }}>
    <AuthProvider><App /></AuthProvider>
  </ConfigProvider>,
);
