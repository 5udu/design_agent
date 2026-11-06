import React from 'react';
import {createRoot} from 'react-dom/client';
import {AuthApp} from './features/auth/AuthApp';
import './styles.css';
createRoot(document.getElementById('root')!).render(<React.StrictMode><AuthApp/></React.StrictMode>);
