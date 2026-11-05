import React from 'react';
import { createRoot } from 'react-dom/client';
import { WeixinConnect } from '../../src/ui/weixin-connect';
import '../../src/ui/style.css';
import './test.css';

createRoot(document.getElementById('root')!).render(
  <main className="weixin-test"><WeixinConnect space="local" runtime="local" computerName="这台 Mac" /></main>
);
