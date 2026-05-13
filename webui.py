#!/usr/bin/env python3
"""Web UI for Backlink Publisher Pipeline - Enhanced UI Mode"""

from flask import Flask, request, render_template_string, jsonify, session, redirect, url_for
import subprocess
import json
import os
import re
import sys
import requests
import uuid
import random
import threading
from pathlib import Path
from urllib.parse import urlparse, urljoin, urlencode
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.executors.pool import ThreadPoolExecutor as APSThreadPoolExecutor

# Import config utilities
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))
from backlink_publisher.config import load_config, save_config, load_blogger_token
from backlink_publisher import checkpoint as _checkpoint_mod

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'backlink-publisher-secret-' + str(uuid.uuid4()))
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=15)
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# ── APScheduler (串行，max_workers=1 防止同時多篇發布) ────────────────────────
_scheduler = BackgroundScheduler(
    executors={'default': APSThreadPoolExecutor(max_workers=1)},
    job_defaults={'misfire_grace_time': 3600},
)

HTML = '''
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Backlink Publisher</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.0/font/bootstrap-icons.css">
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <style>
        :root {
            --primary: #4f46e5;
            --primary-dark: #4338ca;
            --secondary: #6b7280;
            --success: #10b981;
            --danger: #ef4444;
            --warning: #f59e0b;
            --info: #3b82f6;
            --light: #f9fafb;
            --dark: #1f2937;
            --gradient: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        }
        
        body { 
            font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, sans-serif; 
            background: linear-gradient(180deg, #f3f4f6 0%, #e5e7eb 100%);
            min-height: 100vh;
            padding: 20px;
        }
        
        .navbar {
            background: var(--gradient);
            padding: 1rem 2rem;
            border-radius: 12px;
            margin-bottom: 24px;
            box-shadow: 0 10px 40px rgba(102, 126, 234, 0.3);
        }
        
        .navbar h1 {
            color: white;
            margin: 0;
            font-size: 1.5rem;
            font-weight: 600;
        }
        
        .card {
            background: white;
            border: none;
            border-radius: 16px;
            box-shadow: 0 4px 20px rgba(0,0,0,0.08);
            margin-bottom: 20px;
            overflow: hidden;
        }
        
        .card-header {
            background: linear-gradient(135deg, #f9fafb 0%, #f3f4f6 100%);
            padding: 16px 24px;
            border-bottom: 1px solid #e5e7eb;
            font-weight: 600;
            color: var(--dark);
        }
        
        .card-body {
            padding: 24px;
        }
        
        .form-label {
            font-weight: 500;
            color: var(--dark);
            margin-bottom: 8px;
        }
        
        .form-control, .form-select {
            border: 2px solid #e5e7eb;
            border-radius: 10px;
            padding: 12px 16px;
            transition: all 0.3s ease;
        }
        
        .form-control:focus, .form-select:focus {
            border-color: var(--primary);
            box-shadow: 0 0 0 3px rgba(79, 70, 229, 0.1);
        }
        
        .btn {
            border-radius: 10px;
            padding: 12px 24px;
            font-weight: 500;
            transition: all 0.3s ease;
        }
        
        .btn-primary {
            background: var(--gradient);
            border: none;
            color: white;
        }
        
        .btn-primary:hover {
            transform: translateY(-2px);
            box-shadow: 0 8px 20px rgba(102, 126, 234, 0.4);
        }
        
        .btn-success {
            background: linear-gradient(135deg, #10b981 0%, #059669 100%);
            border: none;
        }
        
        .btn-success:hover {
            transform: translateY(-2px);
            box-shadow: 0 8px 20px rgba(16, 185, 129, 0.4);
        }
        
        .url-input-group {
            background: #fdf6e3;
            border-radius: 12px;
            padding: 16px;
            margin-bottom: 16px;
            border: 2px dashed #f59e0b33;
        }
        
        .url-item {
            display: flex;
            align-items: center;
            gap: 12px;
            margin-bottom: 12px;
            background: white;
            padding: 12px;
            border-radius: 10px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.05);
        }
        
        .url-badge {
            padding: 6px 12px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: 600;
        }
        
        .url-badge.main {
            background: linear-gradient(135deg, #4f46e5 0%, #7c3aed 100%);
            color: white;
        }
        
        .url-badge.extra {
            background: #e5e7eb;
            color: var(--secondary);
        }
        
        .config-section {
            background: linear-gradient(135deg, #f9fafb 0%, #f3f4f6 100%);
            border-radius: 12px;
            padding: 20px;
            margin-top: 20px;
            border: 1px solid #e5e7eb;
        }
        
        .config-section h5 {
            color: var(--dark);
            margin-bottom: 16px;
            font-weight: 600;
        }
        
        .info-box {
            background: linear-gradient(135deg, #dbeafe 0%, #bfdbfe 100%);
            border-radius: 12px;
            padding: 16px;
            margin: 16px 0;
            border-left: 4px solid var(--info);
        }
        
        .info-box h6 {
            color: var(--info);
            margin-bottom: 8px;
        }
        
        .result-card {
            background: white;
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 16px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.06);
            border-left: 4px solid var(--success);
        }
        
        .result-card h5 {
            color: var(--dark);
            margin-bottom: 12px;
        }
        
        .link-count-badge {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 6px 14px;
            border-radius: 20px;
            font-weight: 600;
            font-size: 14px;
        }
        
        .link-count-badge.valid {
            background: linear-gradient(135deg, #d1fae5 0%, #a7f3d0 100%);
            color: #065f46;
        }
        
        .link-count-badge.invalid {
            background: linear-gradient(135deg, #fee2e2 0%, #fecaca 100%);
            color: #991b1b;
        }
        
        .link-table {
            width: 100%;
            border-collapse: separate;
            border-spacing: 0;
            margin-top: 16px;
            font-size: 13px;
        }
        
        .link-table th {
            background: #f9fafb;
            padding: 12px;
            text-align: left;
            font-weight: 600;
            color: var(--dark);
            border-bottom: 2px solid #e5e7eb;
        }
        
        .link-table td {
            padding: 12px;
            border-bottom: 1px solid #f3f4f6;
        }
        
        .link-table tr:hover {
            background: #f9fafb;
        }
        
        .type-badge {
            padding: 4px 10px;
            border-radius: 12px;
            font-size: 11px;
            font-weight: 600;
        }
        
        .type-badge.required {
            background: #d1fae5;
            color: #065f46;
        }
        
        .type-badge.optional {
            background: #f3f4f6;
            color: var(--secondary);
        }
        
        .content-preview {
            background: #1e1e1e;
            color: #e5e7eb;
            padding: 20px;
            border-radius: 12px;
            font-family: 'Consolas', monospace;
            font-size: 13px;
            line-height: 1.8;
            max-height: 400px;
            overflow-y: auto;
        }
        
        .content-preview a {
            color: #60a5fa;
            text-decoration: underline;
        }
        
        .content-preview h1, .content-preview h2, .content-preview h3 {
            color: #fbbf24;
            margin: 16px 0 8px;
        }
        
        .content-preview hr {
            border-color: #4b5563;
            margin: 16px 0;
        }
        
        .tag-list {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            margin: 12px 0;
        }
        
        .tag-item {
            background: linear-gradient(135deg, #e0e7ff 0%, #c7d2fe 100%);
            color: #4338ca;
            padding: 4px 12px;
            border-radius: 16px;
            font-size: 12px;
            font-weight: 500;
        }
        
        .error-box {
            background: linear-gradient(135deg, #fee2e2 0%, #fecaca 100%);
            border-radius: 12px;
            padding: 16px;
            border-left: 4px solid var(--danger);
            color: #991b1b;
        }
        
        .success-box {
            background: linear-gradient(135deg, #d1fae5 0%, #a7f3d0 100%);
            border-radius: 12px;
            padding: 16px;
            border-left: 4px solid var(--success);
            color: #065f46;
        }
        
        .meta-info {
            display: flex;
            gap: 16px;
            flex-wrap: wrap;
            margin-bottom: 12px;
            font-size: 13px;
            color: var(--secondary);
        }
        
        .meta-info span {
            display: flex;
            align-items: center;
            gap: 4px;
        }
        
        details > summary {
            cursor: pointer;
            padding: 12px 16px;
            background: #f9fafb;
            border-radius: 8px;
            font-weight: 500;
            color: var(--primary);
            transition: all 0.2s;
        }
        
        details > summary:hover {
            background: #f3f4f6;
        }
        
        details[open] > summary {
            margin-bottom: 16px;
        }
        
        .btn-group-actions {
            display: flex;
            gap: 12px;
            margin-top: 20px;
            flex-wrap: wrap;
        }
        
        .nav-tabs-custom {
            border-bottom: 2px solid #e5e7eb;
            margin-bottom: 20px;
        }
        
        .nav-tabs-custom .nav-link {
            border: none;
            color: var(--secondary);
            padding: 12px 20px;
            font-weight: 500;
        }
        
        .nav-tabs-custom .nav-link.active {
            color: var(--primary);
            border-bottom: 3px solid var(--primary);
            background: transparent;
        }
        
        .nav-tabs-custom .nav-link:hover {
            color: var(--primary);
        }
        
        .history-item {
            padding: 16px;
            border-radius: 12px;
            margin-bottom: 12px;
            background: linear-gradient(135deg, #f9fafb 0%, #f3f4f6 100%);
            border-left: 4px solid var(--primary);
            cursor: pointer;
            transition: all 0.3s;
        }
        
        .history-item:hover {
            transform: translateX(4px);
            box-shadow: 0 4px 12px rgba(0,0,0,0.1);
        }
        
        .history-item.success {
            border-left-color: var(--success);
        }
        
        .history-item.failed {
            border-left-color: var(--danger);
        }
        
        .editor-container {
            background: white;
            border-radius: 12px;
            padding: 20px;
            margin: 16px 0;
            box-shadow: 0 2px 8px rgba(0,0,0,0.06);
        }
        
        .editor-toolbar {
            display: flex;
            gap: 8px;
            padding: 8px;
            background: #f9fafb;
            border-radius: 8px;
            margin-bottom: 12px;
            flex-wrap: wrap;
        }
        
        .editor-btn {
            padding: 6px 12px;
            border: 1px solid #e5e7eb;
            border-radius: 6px;
            background: white;
            cursor: pointer;
            font-size: 13px;
            transition: all 0.2s;
        }
        
        .editor-btn:hover {
            background: var(--primary);
            color: white;
            border-color: var(--primary);
        }
        
        .rich-editor {
            min-height: 300px;
            padding: 16px;
            border: 2px solid #e5e7eb;
            border-radius: 8px;
            line-height: 1.8;
        }
        
        .rich-editor:focus {
            outline: none;
            border-color: var(--primary);
        }
        
        .publish-status {
            padding: 16px;
            border-radius: 12px;
            margin: 12px 0;
        }
        
        .publish-status.success {
            background: linear-gradient(135deg, #d1fae5 0%, #a7f3d0 100%);
            border-left: 4px solid var(--success);
        }
        
        .publish-status.error {
            background: linear-gradient(135deg, #fee2e2 0%, #fecaca 100%);
            border-left: 4px solid var(--danger);
        }
        
        .publish-status.pending {
            background: linear-gradient(135deg, #fef3c7 0%, #fde68a 100%);
            border-left: 4px solid var(--warning);
        }
        
        .status-badge {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: 600;
        }
        
        .status-badge.success {
            background: #d1fae5;
            color: #065f46;
        }
        
        .status-badge.error {
            background: #fee2e2;
            color: #991b1b;
        }
        
        .status-badge.pending {
            background: #fef3c7;
            color: #92400e;
        }
        
        @media (max-width: 768px) {
            body { padding: 10px; }
            .card-body { padding: 16px; }
            .btn-group-actions { flex-direction: column; }
            .btn-group-actions .btn { width: 100%; }
        }

        /* ── Step Progress Bar ─────────────────────────────────────── */
        .step-bar {
            display: flex;
            align-items: center;
            margin-bottom: 20px;
            padding: 16px 24px;
            background: white;
            border-radius: 14px;
            box-shadow: 0 2px 12px rgba(0,0,0,0.06);
            overflow-x: auto;
        }

        .step-item {
            display: flex;
            align-items: center;
            flex-shrink: 0;
        }

        .step-circle {
            width: 32px;
            height: 32px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 13px;
            font-weight: 700;
            flex-shrink: 0;
            transition: all 0.3s;
        }

        .step-circle.done {
            background: var(--success);
            color: white;
        }

        .step-circle.active {
            background: var(--primary);
            color: white;
            box-shadow: 0 0 0 4px rgba(79,70,229,0.2);
        }

        .step-circle.pending {
            background: #e5e7eb;
            color: #9ca3af;
        }

        .step-label {
            margin-left: 8px;
            font-size: 13px;
            font-weight: 600;
            white-space: nowrap;
        }

        .step-label.done   { color: var(--success); }
        .step-label.active { color: var(--primary); }
        .step-label.pending { color: #9ca3af; }

        .step-connector {
            flex: 1;
            height: 2px;
            margin: 0 12px;
            min-width: 24px;
            border-radius: 2px;
        }

        .step-connector.done   { background: var(--success); }
        .step-connector.active { background: linear-gradient(90deg, var(--success) 0%, #e5e7eb 100%); }
        .step-connector.pending { background: #e5e7eb; }
    </style>
</head>
<body>
    <nav class="navbar" style="display:flex;justify-content:space-between;align-items:center;">
        <h1><i class="bi bi-link-45deg me-2"></i>Backlink Publisher</h1>
        <div style="display:flex;gap:8px;align-items:center;">
            {% if blogger_token_status %}
            {% set ts = blogger_token_status %}
            {% if ts.state == 'ok' %}
            <span title="{{ ts.label }}" style="display:flex;align-items:center;gap:5px;
                background:rgba(16,185,129,0.2);border:1px solid rgba(16,185,129,0.5);
                color:white;padding:4px 10px;border-radius:20px;font-size:12px;">
                <i class="bi bi-shield-check"></i> Blogger 已连接
            </span>
            {% elif ts.state == 'expiring' %}
            <a href="/settings" title="{{ ts.label }}" style="display:flex;align-items:center;gap:5px;
                background:rgba(245,158,11,0.25);border:1px solid rgba(245,158,11,0.6);
                color:white;padding:4px 10px;border-radius:20px;font-size:12px;text-decoration:none;">
                <i class="bi bi-exclamation-triangle"></i> {{ ts.label }}
            </a>
            {% elif ts.state == 'expired' %}
            <a href="/settings/blogger/oauth-start" method="post" title="{{ ts.label }}" style="display:flex;align-items:center;gap:5px;
                background:rgba(239,68,68,0.25);border:1px solid rgba(239,68,68,0.6);
                color:white;padding:4px 10px;border-radius:20px;font-size:12px;text-decoration:none;">
                <i class="bi bi-exclamation-circle"></i> Token 过期 · 点击重新授权
            </a>
            {% else %}
            <a href="/settings" title="{{ ts.label }}" style="display:flex;align-items:center;gap:5px;
                background:rgba(239,68,68,0.2);border:1px solid rgba(239,68,68,0.5);
                color:white;padding:4px 10px;border-radius:20px;font-size:12px;text-decoration:none;">
                <i class="bi bi-shield-x"></i> Blogger 未授权
            </a>
            {% endif %}
            {% endif %}
            <a href="/settings" class="btn btn-sm" style="background:rgba(255,255,255,0.2);color:white;border:1px solid rgba(255,255,255,0.5);">
                <i class="bi bi-gear me-1"></i>设置
            </a>
            {% if config %}
            <form method="POST" action="/ce:clear" style="margin:0;">
                <button type="submit" class="btn btn-sm" style="background:rgba(255,255,255,0.2);color:white;border:1px solid rgba(255,255,255,0.5);">
                    <i class="bi bi-arrow-counterclockwise me-1"></i>重置
                </button>
            </form>
            {% endif %}
        </div>
    </nav>
    
    <div class="container-fluid" style="max-width: 1100px;">

        {% if incomplete_run %}
        <div class="alert alert-warning alert-dismissible d-flex align-items-center mb-3" role="alert">
          <i class="bi bi-exclamation-triangle-fill me-2 flex-shrink-0"></i>
          <div class="flex-grow-1">
            <strong>未完成的发布任务</strong>
            — {{ incomplete_run.started_at[:19] if incomplete_run.started_at else '' }}，共 {{ incomplete_run.pending_count }} 篇待处理。
            <form action="/checkpoint/resume" method="POST" class="d-inline ms-2">
              <input type="hidden" name="run_id" value="{{ incomplete_run.run_id }}">
              <button type="submit" class="btn btn-warning btn-sm">
                <i class="bi bi-play-fill me-1"></i>恢复发布
              </button>
            </form>
            <form action="/checkpoint/dismiss" method="POST" class="d-inline ms-1">
              <input type="hidden" name="run_id" value="{{ incomplete_run.run_id }}">
              <button type="submit" class="btn btn-outline-secondary btn-sm">忽略</button>
            </form>
          </div>
          <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
        </div>
        {% endif %}

        <!-- Tab Navigation -->
        <ul class="nav nav-tabs-custom" id="mainTabs" role="tablist">
            <li class="nav-item" role="presentation">
                <button class="nav-link {% if not history_active %}active{% endif %}" id="new-tab" data-bs-toggle="tab" data-bs-target="#newPanel" type="button">
                    <i class="bi bi-plus-circle me-2"></i>新建任务
                </button>
            </li>
            <li class="nav-item" role="presentation">
                <button class="nav-link {% if history_active %}active{% endif %}" id="history-tab" data-bs-toggle="tab" data-bs-target="#historyPanel" type="button">
                    <i class="bi bi-calendar-check me-2"></i>草稿 &amp; 历史
                </button>
            </li>
            <li class="nav-item" role="presentation">
                <button class="nav-link {% if batch_tab %}active{% endif %}" id="batch-tab" data-bs-toggle="tab" data-bs-target="#batchPanel" type="button">
                    <i class="bi bi-stack me-2"></i>批量发布
                </button>
            </li>
            <li class="nav-item" role="presentation">
                <button class="nav-link" id="publish-tab" data-bs-toggle="tab" data-bs-target="#publishPanel" type="button">
                    <i class="bi bi-broadcast me-2"></i>发布中心
                </button>
            </li>
        </ul>
        
        <div class="tab-content" id="mainTabsContent">
            <div class="tab-pane fade {% if not history_active %}show active{% endif %}" id="newPanel" role="tabpanel">

        {# Determine current step based on pipeline state #}
        {% if published %}
          {% set cur_step = 4 %}
        {% elif validated %}
          {% set cur_step = 4 %}
        {% elif plans %}
          {% set cur_step = 3 %}
        {% elif config and config.target_url %}
          {% set cur_step = 2 %}
        {% else %}
          {% set cur_step = 1 %}
        {% endif %}

        <div class="step-bar">
            {% for step_num, step_name in [(1,'输入网址'),(2,'生成文章'),(3,'验证内容'),(4,'发布')] %}
              {% if step_num < cur_step %}
                {% set cls = 'done' %}
              {% elif step_num == cur_step %}
                {% set cls = 'active' %}
              {% else %}
                {% set cls = 'pending' %}
              {% endif %}
              <div class="step-item">
                  <div class="step-circle {{ cls }}">
                      {% if cls == 'done' %}<i class="bi bi-check-lg"></i>{% else %}{{ step_num }}{% endif %}
                  </div>
                  <span class="step-label {{ cls }}">{{ step_name }}</span>
              </div>
              {% if not loop.last %}
                {% if step_num < cur_step %}
                  <div class="step-connector done"></div>
                {% elif step_num == cur_step %}
                  <div class="step-connector active"></div>
                {% else %}
                  <div class="step-connector pending"></div>
                {% endif %}
              {% endif %}
            {% endfor %}
        </div>

        <div class="card">
            <div class="card-header">
                <i class="bi bi-globe2 me-2"></i>输入目标网站连结
            </div>
            <div class="card-body">
                <form method="POST" action="/ce:plan">
                    <div class="url-input-group">
                        <div class="url-item">
                            <span class="url-badge main">主</span>
                            <input type="url" class="form-control" name="target_url" placeholder="https://example.com/main-article" value="{{ target_url }}">
                        </div>
                        {% for extra_url in extra_urls %}
                        <div class="url-item">
                            <span class="url-badge extra">附</span>
                            <input type="url" class="form-control" name="url_{{ loop.index }}" placeholder="附加连结 (分页/分类/相关文章)" value="{{ extra_url }}">
                        </div>
                        {% endfor %}
                        <div class="url-item">
                            <span class="url-badge extra">+</span>
                            <input type="url" class="form-control" name="url_new" placeholder="+ 添加更多连结..." style="border: 2px dashed #ccc;">
                        </div>
                    </div>
                    <button type="submit" class="btn btn-primary">
                        <i class="bi bi-search me-2"></i>分析连结
                    </button>
                </form>
            </div>
        </div>
        
        {% if config %}
        <div class="card">
            <div class="card-header" style="display:flex;justify-content:space-between;align-items:center;">
                <span><i class="bi bi-gear me-2"></i>配置参数</span>
                {% if profiles %}
                <div style="display:flex;gap:8px;align-items:center;">
                    <select id="profilePicker" class="form-select form-select-sm" style="width:180px;"
                            onchange="loadProfile(this.value)">
                        <option value="">— 载入配置 —</option>
                        {% for p in profiles %}
                        <option value="{{ loop.index0 }}">{{ p.name }}</option>
                        {% endfor %}
                    </select>
                </div>
                {% endif %}
            </div>
            <div class="card-body">
                <form method="POST" action="/ce:generate" id="configForm">
                    <input type="hidden" name="urls_json" value="{{ urls_json }}">
                    
                    <div class="row">
                        <div class="col-md-6 mb-3">
                            <label class="form-label">目标平台</label>
                            <select class="form-select" name="platform">
                                <option value="medium" {% if config.platform == 'medium' %}selected{% endif %}>Medium</option>
                                <option value="blogger" {% if config.platform == 'blogger' %}selected{% endif %}>Blogger</option>
                                <option value="wordpress" {% if config.platform == 'wordpress' %}selected{% endif %}>WordPress</option>
                            </select>
                        </div>
                        <div class="col-md-6 mb-3">
                            <label class="form-label">连结模式</label>
                            <select class="form-select" name="url_mode">
                                <option value="A" {% if config.url_mode == 'A' %}selected{% endif %}>A - 纯文字</option>
                                <option value="B" {% if config.url_mode == 'B' %}selected{% endif %}>B - 锚文本</option>
                                <option value="C" {% if config.url_mode == 'C' %}selected{% endif %}>C - 品牌+关键词</option>
                            </select>
                        </div>
                    </div>
                    
                    <div class="row">
                        <div class="col-md-6 mb-3">
                            <label class="form-label">发布语言</label>
                            <select class="form-select" name="target_language">
                                <option value="zh-CN" {% if config.target_language == 'zh-CN' %}selected{% endif %}>简体中文</option>
                                <option value="en" {% if config.target_language == 'en' %}selected{% endif %}>English</option>
                                <option value="ru" {% if config.target_language == 'ru' %}selected{% endif %}>Русский (俄文)</option>
                            </select>
                        </div>
                        <div class="col-md-6 mb-3">
                            <label class="form-label">发布模式</label>
                            <select class="form-select" name="publish_mode">
                                <option value="draft" {% if config.publish_mode == 'draft' %}selected{% endif %}>草稿 (Draft)</option>
                                <option value="publish" {% if config.publish_mode == 'publish' %}selected{% endif %}>直接发布 (Publish)</option>
                            </select>
                        </div>
                    </div>
                    
                    <div class="row">
                        <div class="col-md-6 mb-3">
                            <label class="form-label">文章标题 (留空自动生成)</label>
                            <input type="text" class="form-control" name="custom_title" value="{{ config.custom_title }}" placeholder="自定义标题">
                        </div>
                        <div class="col-md-6 mb-3">
                            <label class="form-label">自定义标签 (逗号分隔)</label>
                            <input type="text" class="form-control" name="custom_tags" value="{{ config.custom_tags }}" placeholder="tech,news,guide">
                        </div>
                    </div>
                    
                    <div class="mb-3">
                        <label class="form-label"><i class="bi bi-download me-1"></i>抓取 TDK</label>
                        <select class="form-select" name="fetch_tdk" style="max-width: 300px;">
                            <option value="no" {% if config.fetch_tdk == 'no' %}selected{% endif %}>不抓取</option>
                            <option value="yes" {% if config.fetch_tdk != 'no' %}selected{% endif %}>抓取目标站 TDK (是)</option>
                        </select>
                        <small class="text-muted">自动抓取目标网站的 Title/Description/Keywords 作为文章参考</small>
                    </div>
                    
                    {% if meta_info %}
                    <div class="info-box">
                        <h6><i class="bi bi-info-circle me-1"></i>抓取的页面信息</h6>
                        {% for m in meta_info %}
                        <div style="background: white;padding: 12px;margin: 8px 0;border-radius: 8px;">
                            <strong style="color: var(--primary);">{{ m.url|truncate(60) }}</strong><br>
                            <small style="color: var(--secondary);">{{ m.title[:80] }}</small>
                        </div>
                        {% endfor %}
                    </div>
                    {% endif %}
                    
                    <div class="mt-3 d-flex gap-2 align-items-center flex-wrap">
                        <button type="submit" class="btn btn-success">
                            <i class="bi bi-rocket me-2"></i>生成发布计划
                        </button>
                        <button type="button" class="btn btn-outline-secondary btn-sm"
                                onclick="saveProfilePrompt()" title="将当前设置保存为命名配置">
                            <i class="bi bi-bookmark-plus me-1"></i>保存为配置
                        </button>
                    </div>
                </form>
            </div>
        </div>
        {% endif %}
        
        {% if plans %}
        <div class="card">
            <div class="card-header">
                <i class="bi bi-check-circle me-2"></i>生成完成 - 预览
            </div>
            <div class="card-body">
                {% for plan in plans_list %}
                <div class="result-card">
                    <h5><i class="bi bi-file-text me-2"></i>{{ plan.title }}</h5>
                    
                    <div class="meta-info">
                        <span><i class="bi bi-translate me-1"></i>{{ plan.language }}</span>
                        <span><i class="bi bi-globe me-1"></i>{{ plan.platform }}</span>
                        <span><i class="bi bi-text-center me-1"></i>{{ plan.content_markdown|length }} 字</span>
                        <span class="link-count-badge {% if plan.links|length >= 6 and plan.links|length <= 8 %}valid{% else %}invalid{% endif %}">
                            <i class="bi bi-link-45deg"></i> {{ plan.links|length }} 个外链
                            {% if plan.links|length < 6 or plan.links|length > 8 %}
                            <span style="font-size:11px;">⚠️ 需 6-8</span>
                            {% endif %}
                        </span>
                    </div>
                    
                    <div class="tag-list">
                        {% for tag in plan.tags %}<span class="tag-item">{{ tag }}</span>{% endfor %}
                    </div>
                    
                    <details>
                        <summary><i class="bi bi-eye me-1"></i>HTML 内容预览</summary>
                        <div class="content-preview mt-3" id="preview-{{ loop.index0 }}">
{% set content = plan.content_markdown %}
{% set content = content.replace('## References', '<hr><h3>References</h3>') %}
{% set content = content.replace('## Additional Resources', '<hr><h3>Additional Resources</h3>') %}
{% for link in plan.links %}
{% set content = content.replace('[' ~ link.anchor ~ '](' ~ link.url ~ ')', '<a href="' ~ link.url ~ '" target="_blank">' ~ link.anchor ~ '</a>') %}
{% endfor %}
{{ content|safe }}
                        </div>
                    </details>

                    <!-- Inline Editor -->
                    <div style="margin-top:10px;">
                        <button type="button" class="btn btn-sm btn-outline-secondary"
                                onclick="toggleEditor({{ loop.index0 }})"
                                id="editBtn-{{ loop.index0 }}">
                            <i class="bi bi-pencil me-1"></i>编辑内容
                        </button>
                        <div id="editor-{{ loop.index0 }}" style="display:none;margin-top:10px;">
                            <textarea id="editorArea-{{ loop.index0 }}"
                                      rows="12" class="form-control"
                                      style="font-family:monospace;font-size:12px;line-height:1.6;"
                                      onkeyup="markDirty({{ loop.index0 }})">{{ plan.content_markdown }}</textarea>
                            <div style="margin-top:8px;display:flex;gap:8px;">
                                <button type="button" class="btn btn-sm btn-primary"
                                        onclick="saveEdit({{ loop.index0 }})">
                                    <i class="bi bi-check2 me-1"></i>确认修改
                                </button>
                                <button type="button" class="btn btn-sm btn-outline-secondary"
                                        onclick="cancelEdit({{ loop.index0 }}, {{ plan.content_markdown | tojson }})">
                                    <i class="bi bi-x me-1"></i>取消
                                </button>
                                <small id="editStatus-{{ loop.index0 }}" class="text-muted" style="line-height:30px;"></small>
                            </div>
                        </div>
                    </div>
                    
                    <details>
                        <summary><i class="bi bi-link-45deg me-1"></i>外链列表 ({{ plan.links|length }} 个)</summary>
                        <table class="link-table">
                            <thead>
                                <tr>
                                    <th>类型</th>
                                    <th>锚文本</th>
                                    <th>网址</th>
                                </tr>
                            </thead>
                            <tbody>
                                {% for link in plan.links %}
                                <tr>
                                    <td><span class="type-badge {% if link.required %}required{% else %}optional{% endif %}">{% if link.required %}必要{% else %}辅助{% endif %}</span></td>
                                    <td>{{ link.anchor }}</td>
                                    <td><a href="{{ link.url }}" target="_blank" style="color: var(--primary);">{{ link.url|truncate(50) }}</a></td>
                                </tr>
                                {% endfor %}
                            </tbody>
                        </table>
                    </details>
                </div>
                {% endfor %}
                
                <div class="btn-group-actions">
                    <form method="POST" action="/ce:validate" style="display:inline;">
                        <input type="hidden" name="plans" value="{{ plans }}">
                        <button type="submit" class="btn btn-secondary">
                            <i class="bi bi-check2-square me-2"></i>验证内容
                        </button>
                    </form>
                    <form method="POST" action="/ce:publish" style="display:inline;">
                        <input type="hidden" name="plans" value="{{ plans }}">
                        <input type="hidden" name="platform" value="{{ config.platform }}">
                        <div class="input-group" style="display:inline-flex;gap:0;">
                            <select name="publish_mode" class="form-select" style="width:110px;border-radius:10px 0 0 10px;">
                                <option value="draft">存草稿</option>
                                <option value="publish">正式发布</option>
                            </select>
                            <button type="submit" class="btn btn-success" style="border-radius:0 10px 10px 0;">
                                <i class="bi bi-upload me-2"></i>发布
                            </button>
                        </div>
                    </form>
                </div>
            </div>
        </div>
        {% endif %}
        
        {% if validated %}
        <div class="card">
            <div class="card-header">
                <i class="bi bi-check-circle-fill me-2"></i>验证通过
            </div>
            <div class="card-body">
                <div class="success-box mb-3">
                    <i class="bi bi-check-circle me-2"></i>内容验证成功！
                </div>
                <form method="POST" action="/ce:publish">
                    <input type="hidden" name="plans" value="{{ validated }}">
                    <input type="hidden" name="platform" value="{{ config.platform }}">
                    <div class="input-group" style="max-width:320px;">
                        <select name="publish_mode" class="form-select" style="border-radius:10px 0 0 10px;">
                            <option value="draft">存草稿</option>
                            <option value="publish">正式发布</option>
                        </select>
                        <button type="submit" class="btn btn-success" style="border-radius:0 10px 10px 0;">
                            <i class="bi bi-upload me-2"></i>发布到 {{ config.platform }}
                        </button>
                    </div>
                    <div style="font-size:12px;color:#6b7280;margin-top:6px;">
                        「存草稿」= 保存到平台草稿箱，「正式发布」= 立即公开上线
                    </div>
                </form>
            </div>
        </div>
        {% endif %}
        
        {% if published %}
        <div class="card">
            <div class="card-header">
                <i class="bi bi-trophy me-2"></i>发布结果
            </div>
            <div class="card-body">
                <div class="success-box mb-3">
                    <i class="bi bi-check-circle me-2"></i>
                    发布成功！
                </div>
                {% if publish_results %}
                {% for r in publish_results %}
                {% set article_url = r.published_url or r.draft_url %}
                <div class="result-card" style="border-left-color:{% if r.status == 'published' %}var(--success){% else %}var(--warning){% endif %};">
                    <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:8px;">
                        <div>
                            <div style="font-weight:600;color:var(--dark);margin-bottom:6px;">{{ r.title }}</div>
                            <span class="status-badge {% if r.status == 'published' %}success{% else %}pending{% endif %}">
                                {% if r.status == 'published' %}<i class="bi bi-check-circle me-1"></i>已发布
                                {% elif r.status == 'drafted' %}<i class="bi bi-clock me-1"></i>草稿
                                {% else %}{{ r.status }}{% endif %}
                            </span>
                        </div>
                        {% if article_url %}
                        <a href="{{ article_url }}" target="_blank" rel="noopener"
                           class="btn btn-success btn-sm" style="align-self:center;">
                            <i class="bi bi-box-arrow-up-right me-1"></i>查看文章
                        </a>
                        {% endif %}
                    </div>
                    {% if article_url %}
                    <div style="margin-top:10px;padding:8px 12px;background:#f9fafb;border-radius:6px;
                                font-size:12px;word-break:break-all;border:1px solid #e5e7eb;">
                        <i class="bi bi-link-45deg me-1" style="color:var(--primary);"></i>
                        <a href="{{ article_url }}" target="_blank" rel="noopener"
                           style="color:var(--primary);">{{ article_url }}</a>
                    </div>
                    {% endif %}
                </div>
                {% endfor %}
                {% endif %}
                <details style="margin-top:12px;">
                    <summary style="font-size:12px;color:var(--secondary);cursor:pointer;">原始输出</summary>
                    <pre class="content-preview mt-2" style="font-size:11px;">{{ published }}</pre>
                </details>
            </div>
        </div>
        {% endif %}
        
        {% if error %}
        <div class="card">
            <div class="card-header" style="background: linear-gradient(135deg, #fee2e2 0%, #fecaca 100%);">
                <i class="bi bi-exclamation-triangle me-2"></i>错误
            </div>
            <div class="card-body">
                <div class="error-box">{{ error }}</div>
            </div>
        </div>
        {% endif %}
        
            </div><!-- End newPanel tab -->
            
            <!-- History Panel -->
            <div class="tab-pane fade {% if history_active %}show active{% endif %}" id="historyPanel" role="tabpanel">
                <!-- ── 草稿队列 ── -->
                <div class="card mb-3">
                    <div class="card-header d-flex justify-content-between align-items-center">
                        <span><i class="bi bi-calendar-check me-2"></i>草稿队列</span>
                        {% set sched_settings = draft_queue | selectattr('status','equalto','scheduled') | list %}
                        <small class="text-muted">{{ draft_queue | length }} 项 · {{ sched_settings | length }} 已排程</small>
                    </div>
                    <div class="card-body">
                        {% if draft_queue %}
                        {% for item in draft_queue %}
                        {% set s = item.status %}
                        <div class="history-item {% if s == 'published' %}success{% elif s == 'failed' %}failed{% elif s == 'scheduled' %}pending{% else %}{% endif %}" style="{% if s == 'scheduled' %}border-left-color:var(--info);{% endif %}">
                            <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px;">
                                <div style="flex:1;min-width:0;">
                                    <strong style="word-break:break-all;font-size:14px;">{{ item.target_url }}</strong>
                                    <br><small style="color:var(--secondary);">
                                        加入 {{ item.created_at }}
                                        {% if item.platform %} · <i class="bi bi-broadcast"></i> {{ item.platform }}{% endif %}
                                        · {{ item.publish_mode or 'draft' }}
                                    </small>
                                    {% if item.scheduled_at and s == 'scheduled' %}
                                    <br><small style="color:var(--info);font-weight:600;">
                                        <i class="bi bi-alarm me-1"></i>排程：{{ item.scheduled_at[:16] | replace('T',' ') }}
                                    </small>
                                    {% endif %}
                                </div>
                                <span class="status-badge {% if s == 'published' %}success{% elif s == 'failed' %}error{% elif s == 'scheduled' %}pending{% else %}{% endif %}" style="flex-shrink:0;{% if s == 'scheduled' %}background:#dbeafe;color:var(--info);{% endif %}">
                                    {% if s == 'published' %}<i class="bi bi-check-circle"></i> 已发布
                                    {% elif s == 'failed' %}<i class="bi bi-x-circle"></i> 失败
                                    {% elif s == 'scheduled' %}<i class="bi bi-alarm"></i> 已排程
                                    {% else %}<i class="bi bi-hourglass-split"></i> 待排程{% endif %}
                                </span>
                            </div>

                            {% if item.error %}
                            <div style="margin-top:6px;font-size:12px;color:var(--danger);background:#fee2e2;padding:6px 10px;border-radius:6px;">
                                <i class="bi bi-exclamation-triangle me-1"></i>{{ item.error[:200] }}
                            </div>
                            {% endif %}

                            {% if item.article_urls %}
                            <div style="margin-top:8px;display:flex;flex-direction:column;gap:4px;">
                                {% for url in item.article_urls %}
                                <a href="{{ url }}" target="_blank" rel="noopener"
                                   style="font-size:12px;color:var(--primary);word-break:break-all;">
                                    <i class="bi bi-box-arrow-up-right me-1"></i>{{ url }}
                                </a>
                                {% endfor %}
                            </div>
                            {% endif %}

                            <!-- 操作栏 -->
                            <div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap;align-items:flex-end;">
                                {% if s == 'pending' %}
                                <!-- 排程表单 -->
                                <form method="POST" action="/ce:draft/schedule" style="margin:0;display:flex;gap:6px;align-items:flex-end;flex-wrap:wrap;">
                                    <input type="hidden" name="id" value="{{ item.id }}">
                                    <div>
                                        <label style="font-size:11px;color:var(--secondary);display:block;margin-bottom:2px;">排程时间</label>
                                        <input type="datetime-local" name="scheduled_at"
                                               class="form-control form-control-sm"
                                               style="width:180px;"
                                               min="{{ now_iso }}"
                                               value="{{ suggested_next }}">
                                    </div>
                                    <button type="submit" class="btn btn-sm btn-primary">
                                        <i class="bi bi-alarm me-1"></i>排程发布
                                    </button>
                                </form>
                                <form method="POST" action="/ce:draft/publish-now" style="margin:0;">
                                    <input type="hidden" name="id" value="{{ item.id }}">
                                    <button type="submit" class="btn btn-sm btn-success"
                                            onclick="return confirm('立即发布此草稿？')">
                                        <i class="bi bi-send me-1"></i>立即发布
                                    </button>
                                </form>
                                {% elif s == 'scheduled' %}
                                <form method="POST" action="/ce:draft/cancel" style="margin:0;">
                                    <input type="hidden" name="id" value="{{ item.id }}">
                                    <button type="submit" class="btn btn-sm btn-outline-warning">
                                        <i class="bi bi-x-circle me-1"></i>取消排程
                                    </button>
                                </form>
                                {% endif %}
                                <!-- 删除 -->
                                <form method="POST" action="/ce:draft/delete" style="margin:0;"
                                      onsubmit="return confirm('确定删除此草稿？')">
                                    <input type="hidden" name="id" value="{{ item.id }}">
                                    <button type="submit" class="btn btn-sm btn-outline-danger">
                                        <i class="bi bi-trash"></i>
                                    </button>
                                </form>
                            </div>
                        </div>
                        {% endfor %}
                        {% else %}
                        <div style="text-align:center;padding:30px;color:var(--secondary);">
                            <i class="bi bi-calendar-plus" style="font-size:36px;"></i>
                            <p style="margin-top:10px;">草稿栏暂无任务<br><small>在发布中心点击「加入草稿栏」即可排程</small></p>
                        </div>
                        {% endif %}
                    </div>
                </div>

                <!-- ── 发布历史 ── -->
                <div class="card">
                    <div class="card-header">
                        <i class="bi bi-clock-history me-2"></i>发布历史
                    </div>
                    <div class="card-body">
                        {% if history %}
                        {% for item in history %}
                        {% set s = item.status %}
                        <div class="history-item {% if s in ('success','published') %}success{% elif s == 'failed' %}failed{% else %}pending{% endif %}">
                            <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px;">
                                <div style="flex:1;min-width:0;">
                                    <strong style="word-break:break-all;font-size:14px;">{{ item.target_url }}</strong>
                                    <br><small style="color:var(--secondary);">{{ item.created_at }}
                                    {% if item.platform %} · <i class="bi bi-broadcast"></i> {{ item.platform }}{% endif %}
                                    {% if item.language %} · {{ item.language }}{% endif %}
                                    </small>
                                </div>
                                <span class="status-badge {% if s in ('success','published') %}success{% elif s == 'drafted' %}pending{% elif s == 'failed' %}error{% else %}pending{% endif %}" style="flex-shrink:0;">
                                    {% if s in ('success','published') %}<i class="bi bi-check-circle"></i> 已发布
                                    {% elif s == 'drafted' %}<i class="bi bi-clock"></i> 草稿
                                    {% elif s == 'failed' %}<i class="bi bi-x-circle"></i> 失败
                                    {% else %}<i class="bi bi-clock"></i> {{ s }}{% endif %}
                                </span>
                            </div>
                            {% if item.article_urls %}
                            <div style="margin-top:8px;display:flex;flex-direction:column;gap:4px;">
                                {% for url in item.article_urls %}
                                <a href="{{ url }}" target="_blank" rel="noopener"
                                   style="font-size:12px;color:var(--primary);word-break:break-all;">
                                    <i class="bi bi-box-arrow-up-right me-1"></i>{{ url }}
                                </a>
                                {% endfor %}
                            </div>
                            {% endif %}
                            {% if item.error %}
                            <div style="margin-top:6px;font-size:12px;color:var(--danger);background:#fee2e2;padding:6px 10px;border-radius:6px;">
                                <i class="bi bi-exclamation-triangle me-1"></i>{{ item.error[:200] }}
                            </div>
                            {% endif %}
                            <div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap;align-items:center;">
                                <form method="POST" action="/ce:history/reuse" style="margin:0;">
                                    <input type="hidden" name="target_url" value="{{ item.target_url }}">
                                    <button type="submit" class="btn btn-sm btn-outline-primary">
                                        <i class="bi bi-arrow-repeat me-1"></i>重新外链
                                    </button>
                                </form>
                                <form method="POST" action="/ce:history/update-status" style="margin:0;display:flex;gap:4px;">
                                    <input type="hidden" name="id" value="{{ item.id }}">
                                    <select name="status" class="form-select form-select-sm" style="width:100px;">
                                        <option value="drafted" {% if s == 'drafted' %}selected{% endif %}>草稿</option>
                                        <option value="published" {% if s in ('published','success') %}selected{% endif %}>已发布</option>
                                        <option value="failed" {% if s == 'failed' %}selected{% endif %}>失败</option>
                                    </select>
                                    <button type="submit" class="btn btn-sm btn-outline-secondary"><i class="bi bi-pencil"></i></button>
                                </form>
                                <form method="POST" action="/ce:history/delete" style="margin:0;"
                                      onsubmit="return confirm('确定删除此条记录？')">
                                    <input type="hidden" name="id" value="{{ item.id }}">
                                    <button type="submit" class="btn btn-sm btn-outline-danger"><i class="bi bi-trash"></i></button>
                                </form>
                            </div>
                        </div>
                        {% endfor %}
                        {% else %}
                        <div style="text-align:center;padding:40px;color:var(--secondary);">
                            <i class="bi bi-inbox" style="font-size:48px;"></i>
                            <p>暂无历史记录</p>
                        </div>
                        {% endif %}
                    </div>
                </div>
            </div>
            
            <!-- Batch Panel -->
            <div class="tab-pane fade {% if batch_tab %}show active{% endif %}" id="batchPanel" role="tabpanel">
                <div class="card">
                    <div class="card-header">
                        <i class="bi bi-stack me-2"></i>批量发布 — 多 URL 一键发布
                    </div>
                    <div class="card-body">
                        {% if profiles %}
                        <div style="margin-bottom:16px;display:flex;align-items:center;gap:10px;">
                            <i class="bi bi-bookmark me-1" style="color:var(--secondary);"></i>
                            <span style="font-size:13px;color:var(--secondary);">快速载入：</span>
                            <select class="form-select form-select-sm" style="width:200px;"
                                    onchange="loadBatchProfile(this.value)">
                                <option value="">— 选择配置 —</option>
                                {% for p in profiles %}
                                <option value="{{ loop.index0 }}">{{ p.name }}</option>
                                {% endfor %}
                            </select>
                        </div>
                        {% endif %}
                        <form method="POST" action="/ce:batch" id="batchForm">
                            <div class="mb-3">
                                <label class="form-label">目标 URL 列表 <small class="text-muted">（每行一个）</small></label>
                                <textarea name="batch_urls" class="form-control" rows="6"
                                    placeholder="https://51acgs.com/category/acg&#10;https://51acgs.com/category/manga&#10;https://51acgs.com/page/about"
                                    style="font-family:monospace;font-size:13px;">{{ batch_urls or '' }}</textarea>
                            </div>
                            <div class="row g-3 mb-3">
                                <div class="col-md-3">
                                    <label class="form-label">目标平台</label>
                                    <select name="platform" class="form-select">
                                        <option value="blogger">Blogger</option>
                                        <option value="medium">Medium</option>
                                    </select>
                                </div>
                                <div class="col-md-3">
                                    <label class="form-label">发布语言</label>
                                    <select name="language" class="form-select">
                                        <option value="zh-CN">简体中文</option>
                                        <option value="en">English</option>
                                        <option value="ru">Русский</option>
                                    </select>
                                </div>
                                <div class="col-md-3">
                                    <label class="form-label">连结模式</label>
                                    <select name="url_mode" class="form-select">
                                        <option value="A">A — 纯文字</option>
                                        <option value="B">B — 分类导航</option>
                                        <option value="C">C — 深度解析</option>
                                    </select>
                                </div>
                                <div class="col-md-3">
                                    <label class="form-label">发布模式</label>
                                    <select name="publish_mode" class="form-select">
                                        <option value="draft">存草稿</option>
                                        <option value="publish">正式发布</option>
                                    </select>
                                </div>
                            </div>
                            <button type="submit" class="btn btn-success">
                                <i class="bi bi-play-circle me-2"></i>开始批量发布
                            </button>
                            <small class="text-muted ms-3">每篇文章约需 30–60 秒，请耐心等候</small>
                        </form>

                        {% if batch_results %}
                        <hr class="my-4">
                        <h6><i class="bi bi-list-check me-2"></i>发布结果</h6>
                        <div style="display:flex;flex-direction:column;gap:10px;margin-top:12px;">
                            {% for r in batch_results %}
                            <div style="padding:12px 16px;border-radius:10px;
                                background:{% if r.status == 'success' %}#f0fdf4{% else %}#fff1f2{% endif %};
                                border-left:4px solid {% if r.status == 'success' %}var(--success){% else %}var(--danger){% endif %};">
                                <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px;">
                                    <div style="flex:1;min-width:0;">
                                        <div style="font-size:13px;font-weight:600;word-break:break-all;">{{ r.url }}</div>
                                        {% if r.title %}
                                        <div style="font-size:12px;color:#4b5563;margin-top:3px;">📄 {{ r.title }}</div>
                                        {% endif %}
                                        {% if r.error %}
                                        <div style="font-size:12px;color:var(--danger);margin-top:4px;">{{ r.error[:150] }}</div>
                                        {% endif %}
                                    </div>
                                    <div style="display:flex;flex-direction:column;align-items:flex-end;gap:6px;flex-shrink:0;">
                                        <span class="status-badge {% if r.status == 'success' %}success{% else %}error{% endif %}">
                                            {% if r.status == 'success' %}✓ 成功{% else %}✗ 失败{% endif %}
                                        </span>
                                        {% if r.article_url %}
                                        <a href="{{ r.article_url }}" target="_blank" rel="noopener"
                                           class="btn btn-success btn-sm">
                                            <i class="bi bi-box-arrow-up-right me-1"></i>查看文章
                                        </a>
                                        {% endif %}
                                    </div>
                                </div>
                                {% if r.article_url %}
                                <div style="margin-top:8px;font-size:11px;word-break:break-all;color:#6b7280;">
                                    <i class="bi bi-link-45deg me-1"></i>{{ r.article_url }}
                                </div>
                                {% endif %}
                            </div>
                            {% endfor %}
                        </div>
                        <div style="margin-top:12px;font-size:13px;color:#6b7280;">
                            共 {{ batch_results|length }} 篇 ·
                            成功 {{ batch_results|selectattr('status','eq','success')|list|length }} 篇 ·
                            失败 {{ batch_results|selectattr('status','eq','failed')|list|length }} 篇
                        </div>
                        {% endif %}
                    </div>
                </div>
            </div>

            <!-- Publish Panel -->
            <div class="tab-pane fade" id="publishPanel" role="tabpanel">
                <div class="card">
                    <div class="card-header">
                        <i class="bi bi-broadcast me-2"></i>发布中心
                    </div>
                    <div class="card-body">
                        {% if ready_to_publish %}
                        <div class="publish-status success">
                            <h5><i class="bi bi-check-circle-fill me-2"></i>内容已验证，可以发布</h5>
                            <p style="margin:8px 0 0;font-size:13px;">目标平台：<strong>{{ ready_to_publish.platform }}</strong></p>
                        </div>
                        <div style="margin-top:16px;display:flex;flex-wrap:wrap;gap:12px;align-items:flex-end;">
                            <form method="POST" action="/ce:publish">
                                <input type="hidden" name="plans" value="{{ ready_to_publish.data }}">
                                <input type="hidden" name="platform" value="{{ ready_to_publish.platform }}">
                                <div class="input-group" style="max-width:280px;">
                                    <select name="publish_mode" class="form-select" style="border-radius:10px 0 0 10px;">
                                        <option value="draft">存草稿</option>
                                        <option value="publish">正式发布</option>
                                    </select>
                                    <button type="submit" class="btn btn-success" style="border-radius:0 10px 10px 0;">
                                        <i class="bi bi-send me-2"></i>立即发布
                                    </button>
                                </div>
                            </form>
                            <form method="POST" action="/ce:draft/save">
                                <input type="hidden" name="plans" value="{{ ready_to_publish.data }}">
                                <input type="hidden" name="platform" value="{{ ready_to_publish.platform }}">
                                <select name="publish_mode" class="form-select form-select-sm mb-1" style="max-width:120px;">
                                    <option value="draft">存草稿</option>
                                    <option value="publish">正式发布</option>
                                </select>
                                <button type="submit" class="btn btn-outline-primary btn-sm" style="width:100%;">
                                    <i class="bi bi-calendar-plus me-1"></i>加入草稿栏
                                </button>
                            </form>
                        </div>
                        {% else %}
                        <div class="publish-status pending">
                            <h5><i class="bi bi-info-circle me-2"></i>尚无待发布内容</h5>
                            <p style="margin:8px 0 0;font-size:13px;">请先在「新建任务」完成文章生成与验证，再来此处发布。</p>
                        </div>
                        {% endif %}
                    </div>
                </div>
            </div>
            
        </div><!-- End tab-content -->
    </div>
    
    <script>
    function loadHistory(id) {
        window.location.href = '/ce:history?id=' + id;
    }

    // ── Inline Article Editor ────────────────────────────────────
    {% if plans_list %}
    let _plansData = {{ plans_list | tojson }};
    {% else %}
    let _plansData = [];
    {% endif %}

    function _rebuildPlansJsonl() {
        return _plansData.map(p => JSON.stringify(p)).join('\\n');
    }
    function _syncPlansFields() {
        const jsonl = _rebuildPlansJsonl();
        document.querySelectorAll('input[name="plans"]').forEach(el => { el.value = jsonl; });
    }
    function toggleEditor(idx) {
        const el = document.getElementById('editor-' + idx);
        const btn = document.getElementById('editBtn-' + idx);
        if (el.style.display === 'none') {
            el.style.display = 'block';
            btn.innerHTML = '<i class="bi bi-eye me-1"></i>收起编辑器';
        } else {
            el.style.display = 'none';
            btn.innerHTML = '<i class="bi bi-pencil me-1"></i>编辑内容';
        }
    }
    function markDirty(idx) {
        const s = document.getElementById('editStatus-' + idx);
        if (s) s.textContent = '（未保存）';
    }
    function saveEdit(idx) {
        const ta = document.getElementById('editorArea-' + idx);
        if (_plansData[idx]) { _plansData[idx].content_markdown = ta.value; _syncPlansFields(); }
        const s = document.getElementById('editStatus-' + idx);
        if (s) { s.textContent = '✓ 已保存'; s.style.color = 'var(--success)'; }
        const preview = document.getElementById('preview-' + idx);
        if (preview) preview.innerHTML = '<em style="color:#6b7280;font-size:12px;">内容已修改</em>';
    }
    function cancelEdit(idx, original) {
        const ta = document.getElementById('editorArea-' + idx);
        ta.value = original;
        if (_plansData[idx]) { _plansData[idx].content_markdown = original; _syncPlansFields(); }
        const s = document.getElementById('editStatus-' + idx);
        if (s) { s.textContent = '已还原'; s.style.color = ''; }
    }

    // ── Campaign Profiles ────────────────────────────────────────
    const _PROFILES = {{ profiles | tojson }};

    function loadProfile(idx) {
        if (idx === '') return;
        const p = _PROFILES[parseInt(idx)];
        if (!p) return;
        const form = document.getElementById('configForm');
        const setVal = (name, val) => {
            const el = form ? form.querySelector('select[name="' + name + '"]')
                            : document.querySelector('select[name="' + name + '"]');
            if (el) el.value = val;
        };
        setVal('platform', p.platform || 'blogger');
        setVal('target_language', p.language || 'zh-CN');
        setVal('url_mode', p.url_mode || 'A');
        setVal('publish_mode', p.publish_mode || 'draft');
        const picker = document.getElementById('profilePicker');
        if (picker) picker.value = '';
    }
    function loadBatchProfile(idx) {
        if (idx === '') return;
        const p = _PROFILES[parseInt(idx)];
        if (!p) return;
        const setVal = (name, val) => {
            const el = document.querySelector('#batchForm select[name="' + name + '"]');
            if (el) el.value = val;
        };
        setVal('platform', p.platform || 'blogger');
        setVal('language', p.language || 'zh-CN');
        setVal('url_mode', p.url_mode || 'A');
        setVal('publish_mode', p.publish_mode || 'draft');
    }
    function saveProfilePrompt() {
        const name = prompt('配置名称（如：51acgs-zh-blogger）：', '');
        if (!name || !name.trim()) return;
        const form = document.getElementById('configForm');
        const getVal = (sel) => {
            const el = form ? form.querySelector('select[name="' + sel + '"]')
                           : document.querySelector('select[name="' + sel + '"]');
            return el ? el.value : '';
        };
        const data = new FormData();
        data.append('profile_name', name.trim());
        data.append('platform', getVal('platform'));
        data.append('language', getVal('target_language'));
        data.append('url_mode', getVal('url_mode'));
        data.append('publish_mode', getVal('publish_mode'));
        fetch('/profiles/save', { method: 'POST', body: data })
            .then(r => r.json())
            .then(d => { if (d.ok) alert('配置「' + name.trim() + '」已保存 ✓'); });
    }

    // ── Loading Overlay ──────────────────────────────────────────
    (function() {
        const MSGS = {
            '/ce:plan':              { text: '分析网址中…',     sub: '正在抓取页面元数据' },
            '/ce:generate':          { text: 'AI 生成文章中…', sub: '调用 AI 生成外链文章，约需 30–60 秒' },
            '/ce:validate':          { text: '验证内容中…',     sub: '检查外链格式与内容合规性' },
            '/ce:publish':           { text: '发布中…',         sub: '正在发布到目标平台，请勿关闭页面' },
            '/ce:publish-real':      { text: '正式发布中…',     sub: '正在写入平台，请勿关闭页面' },
            '/ce:batch':             { text: '批量发布中…',     sub: '正在逐篇生成并发布，每篇约 30–60 秒，请勿关闭页面' },
            '/checkpoint/resume':    { text: '恢复发布中…',     sub: '正在处理未完成的发布任务，可能需要数分钟，请勿关闭页面' },
        };
        document.addEventListener('submit', function(e) {
            const form = e.target;
            const action = (form.getAttribute('action') || '').split('?')[0];
            if (['/ce:clear','/ce:history/delete','/ce:history/update-status'].includes(action)) return;
            const msg = MSGS[action] || { text: '处理中…', sub: '请稍候' };
            document.getElementById('_loadingText').textContent    = msg.text;
            document.getElementById('_loadingSubtext').textContent = msg.sub;
            document.getElementById('_loadingOverlay').style.display = 'flex';
            form.querySelectorAll('[type="submit"]').forEach(function(btn) { btn.disabled = true; });
        });
    })();
    </script>

    <!-- Loading Overlay -->
    <div id="_loadingOverlay" style="display:none;position:fixed;inset:0;background:rgba(15,15,15,0.55);z-index:9999;flex-direction:column;align-items:center;justify-content:center;">
        <div style="background:white;border-radius:20px;padding:40px 48px;text-align:center;max-width:320px;box-shadow:0 24px 64px rgba(0,0,0,0.25);">
            <div class="spinner-border mb-4" style="width:3rem;height:3rem;color:var(--primary);" role="status">
                <span class="visually-hidden">Loading…</span>
            </div>
            <div id="_loadingText" style="font-size:1.1rem;font-weight:700;color:#1f2937;margin-bottom:8px;">处理中…</div>
            <div id="_loadingSubtext" style="font-size:0.85rem;color:#6b7280;line-height:1.5;">请稍候</div>
        </div>
    </div>
</body>
</html>
'''

# ── Settings page template ──────────────────────────────────────────────────
SETTINGS_HTML = '''
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>设置 - Backlink Publisher</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.0/font/bootstrap-icons.css">
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <style>
        :root { --primary:#4f46e5; --gradient:linear-gradient(135deg,#667eea 0%,#764ba2 100%); }
        body { font-family:'Segoe UI',-apple-system,BlinkMacSystemFont,sans-serif;
               background:linear-gradient(180deg,#f3f4f6 0%,#e5e7eb 100%);
               min-height:100vh; padding:20px; }
        .navbar { background:var(--gradient); color:white; padding:16px 24px;
                  border-radius:12px; margin-bottom:24px; }
        .navbar h1 { font-size:1.4rem; font-weight:700; margin:0; color:white; }
        .card { border:none; border-radius:12px; box-shadow:0 2px 8px rgba(0,0,0,.08);
                margin-bottom:20px; }
        .card-header { background:linear-gradient(135deg,#f8fafc,#f1f5f9);
                       border-bottom:1px solid #e2e8f0; font-weight:600;
                       border-radius:12px 12px 0 0!important; padding:14px 20px; }
        .badge-status { display:inline-flex; align-items:center; gap:6px;
                        padding:4px 12px; border-radius:20px; font-size:12px; font-weight:600; }
        .badge-status.ok  { background:#dcfce7; color:#166534; }
        .badge-status.err { background:#fee2e2; color:#991b1b; }
        .badge-status.warn{ background:#fef9c3; color:#854d0e; }
        .blog-id-row { display:grid; grid-template-columns:1fr 1fr auto; gap:8px;
                       align-items:center; margin-bottom:8px; }
        .token-box { font-family:monospace; letter-spacing:.05em; }
        .btn-google {
            display:inline-flex; align-items:center; gap:10px;
            background:#fff; color:#3c4043; border:1px solid #dadce0;
            border-radius:4px; padding:9px 16px; font-size:14px; font-weight:500;
            cursor:pointer; transition:box-shadow .15s;
        }
        .btn-google:hover { box-shadow:0 1px 3px rgba(0,0,0,.2); background:#f8f9fa; }
        .btn-google:disabled { opacity:.5; cursor:not-allowed; }
        .btn-google svg { width:18px; height:18px; flex-shrink:0; }
    </style>
</head>
<body>
    <nav class="navbar" style="display:flex;justify-content:space-between;align-items:center;">
        <h1><i class="bi bi-gear me-2"></i>设置</h1>
        <a href="/" class="btn btn-sm" style="background:rgba(255,255,255,0.2);color:white;border:1px solid rgba(255,255,255,0.5);">
            <i class="bi bi-arrow-left me-1"></i>返回
        </a>
    </nav>

    <div class="container-fluid" style="max-width:860px;">

        {% if flash %}
        <div class="alert alert-{{ flash.type }} alert-dismissible fade show" role="alert">
            {{ flash.msg }}
            <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
        </div>
        {% endif %}

        <!-- ① Blogger OAuth 设置 -->
        <div class="card">
            <div class="card-header">
                <i class="bi bi-google me-2" style="color:#ea4335;"></i>Blogger API — OAuth 授权
            </div>
            <div class="card-body">

                <!-- 连接状态 -->
                <div class="mb-3 d-flex align-items-center gap-3">
                    <span class="fw-semibold" style="font-size:14px;">当前状态：</span>
                    {% if blogger_token %}
                    <span class="badge-status ok"><i class="bi bi-check-circle-fill"></i>已授权</span>
                    <small class="text-muted">Token 文件已存在</small>
                    {% else %}
                    <span class="badge-status err"><i class="bi bi-x-circle-fill"></i>未授权</span>
                    {% endif %}
                </div>

                <!-- 步骤 1：Google Cloud Console OAuth 配置 -->
                <div style="background:#fffbeb;border:1px solid #fde68a;border-radius:8px;padding:14px 16px;margin-bottom:20px;">
                    <p style="font-size:13px;font-weight:600;margin-bottom:10px;color:#92400e;">
                        <i class="bi bi-exclamation-triangle-fill me-1"></i>
                        Step 1 — 在 Google Cloud Console 注册回调网址
                    </p>
                    <p style="font-size:12px;color:#78350f;margin-bottom:10px;">
                        前往 <a href="https://console.cloud.google.com/apis/credentials" target="_blank">API &amp; Services → 凭据</a>，
                        打开你的 OAuth 2.0 客户端 ID，根据你的 <strong>客户端类型</strong> 选择对应操作：
                    </p>

                    <!-- 桌面应用（推荐） -->
                    <div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:6px;padding:10px 14px;margin-bottom:10px;">
                        <p style="font-size:12px;font-weight:700;color:#166534;margin-bottom:6px;">
                            ✓ 推荐：桌面应用 (Desktop application)
                        </p>
                        <p style="font-size:12px;color:#15803d;margin-bottom:8px;">
                            在「已获授权的重定向 URI」中只需添加：
                        </p>
                        <div style="display:flex;align-items:center;gap:8px;max-width:320px;">
                            <input type="text" class="form-control form-control-sm" value="http://localhost" readonly
                                   style="font-family:monospace;font-size:12px;background:#fff;flex:1;">
                            <button class="btn btn-success btn-sm" type="button"
                                    onclick="navigator.clipboard.writeText('http://localhost').then(()=>{this.textContent='✓';setTimeout(()=>{this.innerHTML='<i class=\'bi bi-clipboard\'></i>'},1500)})">
                                <i class="bi bi-clipboard"></i>
                            </button>
                        </div>
                        <p style="font-size:11px;color:#166534;margin-top:6px;margin-bottom:0;">
                            桌面应用只需注册 <code>http://localhost</code>，Google 自动允许任意端口，无需注册完整路径。
                        </p>
                    </div>

                    <!-- Web 应用 -->
                    <div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:6px;padding:10px 14px;">
                        <p style="font-size:12px;font-weight:700;color:#9a3412;margin-bottom:6px;">
                            Web 应用 (Web application) — 需注册完整回调网址：
                        </p>
                        <div class="input-group" style="max-width:520px;">
                            <input type="text" class="form-control form-control-sm"
                                   id="callbackUriDisplay"
                                   value="{{ callback_uri }}" readonly
                                   style="font-family:monospace;font-size:12px;background:#fff;">
                            <button class="btn btn-warning btn-sm" type="button"
                                    onclick="copyUri()" id="copyBtn">
                                <i class="bi bi-clipboard me-1"></i>复制
                            </button>
                        </div>
                        <p style="font-size:11px;color:#9a3412;margin-top:6px;margin-bottom:0;">
                            网址必须与上方完全一致（含端口号），否则会出现 redirect_uri_mismatch。
                        </p>
                    </div>
                </div>

                <!-- 步骤 2：填写凭据 -->
                <form method="POST" action="/settings/blogger/oauth-start"
                      id="oauthCredForm">
                    <p style="font-size:13px;font-weight:600;margin-bottom:10px;color:#374151;">
                        Step 2 — 填入 OAuth 凭据
                    </p>
                    <div class="row g-3 mb-3">
                        <div class="col-md-6">
                            <label class="form-label fw-semibold" style="font-size:13px;">Client ID</label>
                            <input type="text" class="form-control" name="client_id"
                                   placeholder="xxxx.apps.googleusercontent.com"
                                   value="{{ blogger_client_id }}">
                        </div>
                        <div class="col-md-6">
                            <label class="form-label fw-semibold" style="font-size:13px;">Client Secret</label>
                            <div class="input-group">
                                <input type="password" class="form-control" name="client_secret"
                                       id="clientSecretInput"
                                       placeholder="GOCSPX-..."
                                       value="{{ blogger_client_secret }}">
                                <button type="button" class="btn btn-outline-secondary"
                                        onclick="toggleSecret()">
                                    <i class="bi bi-eye" id="secretEye"></i>
                                </button>
                            </div>
                        </div>
                    </div>

                    <!-- 已綁定狀態顯示 -->
                    {% if blogger_client_id %}
                    <div class="mb-3 p-2" style="background:#f0fdf4;border:1px solid #bbf7d0;
                         border-radius:6px;font-size:12px;color:#166534;display:flex;
                         align-items:center;gap:8px;">
                        <i class="bi bi-check-circle-fill"></i>
                        凭据已绑定：<code style="font-size:11px;">{{ blogger_client_id[:24] }}…</code>
                    </div>
                    {% endif %}

                    <!-- 按鈕列 -->
                    <div class="d-flex align-items-center gap-3 flex-wrap mt-3">

                        <!-- 確認綁定（只儲存，不發 OAuth） -->
                        <button type="submit"
                                formaction="/settings/save-blogger-oauth"
                                class="btn btn-primary">
                            <i class="bi bi-check2-circle me-1"></i>确认绑定
                        </button>

                        <!-- Google 登入（儲存 + 發 OAuth） -->
                        <button type="submit"
                                formaction="/settings/blogger/oauth-start"
                                class="btn-google">
                            <svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"
                                 style="width:18px;height:18px;flex-shrink:0;">
                                <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4"/>
                                <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/>
                                <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05"/>
                                <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/>
                            </svg>
                            使用 Google 帐号登入
                        </button>

                        {% if blogger_token %}
                        <button type="button" class="btn btn-outline-secondary btn-sm"
                                onclick="document.getElementById('revokeForm').submit()">
                            <i class="bi bi-trash me-1"></i>撤销现有授权
                        </button>
                        {% endif %}
                    </div>

                    <p class="text-muted mt-2 mb-0" style="font-size:11px;">
                        「确认绑定」只保存凭据；「使用 Google 帐号登入」保存后立即开始授权。
                    </p>
                </form>

                {% if blogger_token %}
                <form id="revokeForm" method="POST" action="/settings/revoke-blogger" style="display:none;"></form>
                {% endif %}

            </div>
        </div>

        <!-- ② Blog ID 映射 -->
        <div class="card" id="blogger-blog-ids">
            <div class="card-header">
                <i class="bi bi-diagram-3 me-2" style="color:#4f46e5;"></i>Blogger Blog ID 映射
            </div>
            <div class="card-body">
                <p class="text-muted" style="font-size:13px;">
                    将每个目标主域名映射到对应的 Blogger Blog ID。<br>
                    Blog ID 可在 Blogger 控制台 URL 中找到：<code>blogger.com/blog/posts/<strong>1234567890</strong></code>
                </p>

                <form method="POST" action="/settings/save-blog-ids" id="blogIdForm">
                    <div id="blogIdRows">
                        {% for domain, blog_id in blog_ids.items() %}
                        <div class="blog-id-row">
                            <input type="text" class="form-control form-control-sm" name="domain[]"
                                   value="{{ domain }}" placeholder="https://your-site.com">
                            <input type="text" class="form-control form-control-sm token-box" name="blog_id[]"
                                   value="{{ blog_id }}" placeholder="1234567890123456789">
                            <button type="button" class="btn btn-outline-danger btn-sm" onclick="removeRow(this)">
                                <i class="bi bi-trash"></i>
                            </button>
                        </div>
                        {% endfor %}
                        <!-- Empty row if no entries -->
                        {% if not blog_ids %}
                        <div class="blog-id-row">
                            <input type="text" class="form-control form-control-sm" name="domain[]"
                                   placeholder="https://your-site.com">
                            <input type="text" class="form-control form-control-sm token-box" name="blog_id[]"
                                   placeholder="1234567890123456789">
                            <button type="button" class="btn btn-outline-danger btn-sm" onclick="removeRow(this)">
                                <i class="bi bi-trash"></i>
                            </button>
                        </div>
                        {% endif %}
                    </div>
                    <div class="d-flex gap-2 mt-3">
                        <button type="button" class="btn btn-outline-secondary btn-sm" onclick="addRow()">
                            <i class="bi bi-plus me-1"></i>新增一行
                        </button>
                        <button type="submit" class="btn btn-primary btn-sm">
                            <i class="bi bi-floppy me-1"></i>保存映射
                        </button>
                    </div>
                </form>
            </div>
        </div>

        <!-- ③ Medium OAuth 授权 & Integration Token -->
        <div class="card">
            <div class="card-header">
                <i class="bi bi-medium me-2"></i>Medium 授权
            </div>
            <div class="card-body">
                <p class="text-muted" style="font-size:13px;">
                    推荐使用 <strong>OAuth 授权</strong>（需要申请 Medium 应用）或使用 <strong>Integration Token</strong>（已停用但仍可用）。
                </p>
                
                <!-- OAuth 授权部分 -->
                {% if not medium_oauth_configured %}
                <div class="alert alert-info" style="margin-bottom:20px;">
                    <strong>🔐 推荐：使用 OAuth 授权</strong><br/>
                    需要先在 <a href="https://medium.com/me/apps" target="_blank">https://medium.com/me/apps</a> 创建应用获得 Client ID 和 Client Secret。
                </div>
                <form method="POST" action="/settings/medium/oauth-start" style="margin-bottom:20px;">
                    <div class="row">
                        <div class="col-md-6">
                            <label class="form-label">Client ID</label>
                            <input type="text" class="form-control" name="client_id" placeholder="你的 Medium Client ID" required>
                        </div>
                        <div class="col-md-6">
                            <label class="form-label">Client Secret</label>
                            <input type="password" class="form-control" name="client_secret" placeholder="你的 Medium Client Secret" required>
                        </div>
                    </div>
                    <div class="mt-3">
                        <button type="submit" class="btn btn-success">
                            <i class="bi bi-box-arrow-up-right me-1"></i>通过 Medium 授权
                        </button>
                    </div>
                </form>
                {% else %}
                <div class="alert alert-success" style="margin-bottom:20px;">
                    <i class="bi bi-check-circle-fill me-2"></i><strong>OAuth 已授权</strong>
                </div>
                <div class="d-flex gap-2" style="margin-bottom:20px;">
                    <form method="POST" action="/settings/clear-medium-oauth" style="margin:0;">
                        <button type="submit" class="btn btn-outline-danger btn-sm">
                            <i class="bi bi-trash me-1"></i>清除 OAuth 授权
                        </button>
                    </form>
                </div>
                {% endif %}
                
                <hr style="margin:20px 0;">
                
                <!-- Integration Token 备选方案 -->
                <p class="text-muted" style="font-size:13px;">
                    <strong>备选：Integration Token</strong>（已停用但仍可用）<br/>
                    如果无法使用 OAuth，可前往 <a href="https://medium.com/me/settings/security" target="_blank">medium.com → 设置 → 安全 → Integration tokens</a> 生成 Token。
                </p>
                <form method="POST" action="/settings/save-medium-token">
                    <div class="mb-3">
                        <label class="form-label">Integration Token</label>
                        <div class="input-group">
                            <input type="password" class="form-control token-box" name="medium_token"
                                   id="mediumTokenInput"
                                   placeholder="请输入 Medium Integration Token"
                                   value="{{ medium_token_masked }}">
                            <button class="btn btn-outline-secondary" type="button"
                                    onclick="toggleToken()">
                                <i class="bi bi-eye" id="eyeIcon"></i>
                            </button>
                        </div>
                    </div>
                    <div class="d-flex gap-2">
                        <button type="submit" class="btn btn-primary btn-sm">
                            <i class="bi bi-floppy me-1"></i>保存 Token
                        </button>
                        {% if medium_token_set %}
                        <form method="POST" action="/settings/clear-medium-token" style="margin:0;">
                            <button type="submit" class="btn btn-outline-danger btn-sm">
                                <i class="bi bi-trash me-1"></i>清除 Token
                            </button>
                        </form>
                        {% endif %}
                    </div>
                </form>
                {% if medium_token_set %}
                <div class="mt-3">
                    <span class="badge-status ok"><i class="bi bi-check-circle-fill"></i>Token 已配置</span>
                    <small class="text-muted ms-2">发布时优先使用 API</small>
                </div>
                {% else %}
                <div class="mt-3">
                    <span class="badge-status warn"><i class="bi bi-exclamation-triangle-fill"></i>Token 未设置</span>
                    <small class="text-muted ms-2">将使用 Playwright 浏览器 fallback</small>
                </div>
                {% endif %}
            </div>
        </div>

        <!-- ④ 配置文件位置 -->
        <div class="card">
            <div class="card-header">
                <i class="bi bi-file-earmark-text me-2"></i>配置文件
            </div>
            <div class="card-body">
                <p class="text-muted mb-1" style="font-size:13px;">配置保存路径：</p>
                <code style="font-size:13px;">{{ config_path }}</code>
                <p class="text-muted mt-2 mb-0" style="font-size:12px;">
                    OAuth Token：<code>{{ token_path }}</code>
                </p>
            </div>
        </div>

        <!-- ④b SEO 锚文本关键词池 -->
        <div class="card">
            <div class="card-header">
                <i class="bi bi-tag me-2"></i>SEO 锚文本配置
                <span class="text-muted" style="font-size:12px;font-weight:400;margin-left:8px;">
                    — 每个 target 站的关键词池（一行一个关键词）
                </span>
            </div>
            <div class="card-body">
                <p class="text-muted" style="font-size:13px;">
                    生成的外链文章会从这里选取关键词作为锚文本，替代裸域名（如 <code>example.com</code>）。
                    空白则回退到裸域名 + WARN 日志。建议每个 target 填写 5–10 个关键词，
                    混合品牌词、行业词和长尾词。
                </p>
                {% if not all_targets %}
                <p class="text-muted" style="font-size:13px;">
                    <i class="bi bi-info-circle me-1"></i>
                    暂无已知 target 站。请先在「Blogger Blog ID」部分添加 target 站的映射，或直接手动编辑
                    <code>{{ config_path }}</code>。
                </p>
                {% else %}
                <form method="POST" action="/settings/save-target-keywords">
                    {% for domain in all_targets %}
                    {% set kws = target_anchor_keywords.get(domain, []) %}
                    <details style="margin-bottom:12px;border:1px solid #e2e8f0;border-radius:8px;overflow:hidden;">
                        <summary style="padding:10px 14px;cursor:pointer;background:#f8fafc;font-size:13px;user-select:none;">
                            <strong>{{ domain }}</strong>
                            <span class="badge {% if kws %}bg-success{% else %}bg-warning text-dark{% endif %} ms-2" style="font-size:11px;">
                                {% if kws %}{{ kws | length }} 个关键词{% else %}未配置{% endif %}
                            </span>
                        </summary>
                        <div style="padding:12px 14px;">
                            <label class="form-label" style="font-size:12px;color:#64748b;">
                                关键词（每行一个）：
                            </label>
                            <textarea
                                class="form-control"
                                name="keywords_{{ loop.index }}"
                                rows="4"
                                style="font-size:13px;font-family:monospace;"
                                placeholder="品牌词&#10;行业关键词&#10;长尾短语..."
                            >{{ kws | join('\n') }}</textarea>
                            <input type="hidden" name="domain_{{ loop.index }}" value="{{ domain }}">
                            <div class="form-text">每行一个关键词，去除前后空白，字符长度 ≤ 60。重复项将自动去重。</div>
                        </div>
                    </details>
                    {% endfor %}
                    <input type="hidden" name="domain_count" value="{{ all_targets | length }}">
                    <button type="submit" class="btn btn-primary btn-sm">
                        <i class="bi bi-save me-1"></i>保存所有关键词池
                    </button>
                </form>
                {% endif %}
            </div>
        </div>

        <!-- ⑤ 排程发布设定 -->
        <div class="card">
            <div class="card-header">
                <i class="bi bi-alarm me-2"></i>排程发布设定
            </div>
            <div class="card-body">
                <p class="text-muted" style="font-size:13px;">
                    控制草稿队列的发布节奏，避免短时间内大量上稿被平台识别。
                </p>
                <form method="POST" action="/settings/schedule">
                    <div class="row g-3">
                        <div class="col-md-6">
                            <label class="form-label">最小发布间隔（小时）</label>
                            <input type="number" class="form-control" name="min_interval_hours"
                                   value="{{ schedule_settings.min_interval_hours }}"
                                   min="0.5" max="168" step="0.5">
                            <div class="form-text">两篇文章发布之间最少间隔小时数（建议 4–24h）</div>
                        </div>
                        <div class="col-md-6">
                            <label class="form-label">随机抖动（±分钟）</label>
                            <input type="number" class="form-control" name="jitter_minutes"
                                   value="{{ schedule_settings.jitter_minutes }}"
                                   min="0" max="120" step="5">
                            <div class="form-text">在间隔基础上随机增减的分钟数，模拟自然节奏</div>
                        </div>
                    </div>
                    <button type="submit" class="btn btn-primary btn-sm mt-3">
                        <i class="bi bi-floppy me-1"></i>保存设定
                    </button>
                </form>
            </div>
        </div>

    </div>

    <script>
    // ── Blog ID 行管理 ───────────────────────────────────────────
    function addRow() {
        const container = document.getElementById('blogIdRows');
        const row = document.createElement('div');
        row.className = 'blog-id-row';
        row.innerHTML = `
            <input type="text" class="form-control form-control-sm" name="domain[]"
                   placeholder="https://your-site.com">
            <input type="text" class="form-control form-control-sm token-box" name="blog_id[]"
                   placeholder="1234567890123456789">
            <button type="button" class="btn btn-outline-danger btn-sm" onclick="removeRow(this)">
                <i class="bi bi-trash"></i>
            </button>`;
        container.appendChild(row);
    }

    function removeRow(btn) {
        btn.closest('.blog-id-row').remove();
    }

    // ── 复制 Redirect URI ────────────────────────────────────────
    function copyUri() {
        const val = document.getElementById('callbackUriDisplay').value;
        navigator.clipboard.writeText(val).then(() => {
            const btn = document.getElementById('copyBtn');
            btn.innerHTML = '<i class="bi bi-check2 me-1"></i>已复制';
            btn.classList.replace('btn-warning', 'btn-success');
            setTimeout(() => {
                btn.innerHTML = '<i class="bi bi-clipboard me-1"></i>复制';
                btn.classList.replace('btn-success', 'btn-warning');
            }, 2000);
        });
    }

    // ── Client Secret 显示切换 ──────────────────────────────────
    function toggleSecret() {
        const input = document.getElementById('clientSecretInput');
        const icon  = document.getElementById('secretEye');
        if (input.type === 'password') {
            input.type = 'text';
            icon.className = 'bi bi-eye-slash';
        } else {
            input.type = 'password';
            icon.className = 'bi bi-eye';
        }
    }

    // ── Medium Token 显示切换 ────────────────────────────────────
    function toggleToken() {
        const input = document.getElementById('mediumTokenInput');
        const icon = document.getElementById('eyeIcon');
        if (input.type === 'password') {
            input.type = 'text';
            icon.className = 'bi bi-eye-slash';
        } else {
            input.type = 'password';
            icon.className = 'bi bi-eye';
        }
    }

    // ── Inline Article Editor ────────────────────────────────────
    // _plansData holds a mutable copy of plans_list; kept in sync across edits.
    {% if plans_list %}
    let _plansData = {{ plans_list | tojson }};
    {% else %}
    let _plansData = [];
    {% endif %}

    function _rebuildPlansJsonl() {
        return _plansData.map(p => JSON.stringify(p)).join('\n');
    }

    function _syncPlansFields() {
        const jsonl = _rebuildPlansJsonl();
        document.querySelectorAll('input[name="plans"]').forEach(el => { el.value = jsonl; });
    }

    function toggleEditor(idx) {
        const el = document.getElementById('editor-' + idx);
        const btn = document.getElementById('editBtn-' + idx);
        if (el.style.display === 'none') {
            el.style.display = 'block';
            btn.innerHTML = '<i class="bi bi-eye me-1"></i>收起编辑器';
        } else {
            el.style.display = 'none';
            btn.innerHTML = '<i class="bi bi-pencil me-1"></i>编辑内容';
        }
    }

    function markDirty(idx) {
        const status = document.getElementById('editStatus-' + idx);
        if (status) status.textContent = '（未保存）';
    }

    function saveEdit(idx) {
        const ta = document.getElementById('editorArea-' + idx);
        const newContent = ta.value;
        if (_plansData[idx]) {
            _plansData[idx].content_markdown = newContent;
            _syncPlansFields();
        }
        const status = document.getElementById('editStatus-' + idx);
        if (status) { status.textContent = '✓ 已保存'; status.style.color = 'var(--success)'; }
        // Update preview
        const preview = document.getElementById('preview-' + idx);
        if (preview) preview.innerHTML = '<em style="color:#6b7280;font-size:12px;">内容已修改，请展开预览查看</em>';
    }

    function cancelEdit(idx, original) {
        const ta = document.getElementById('editorArea-' + idx);
        ta.value = original;
        if (_plansData[idx]) {
            _plansData[idx].content_markdown = original;
            _syncPlansFields();
        }
        const status = document.getElementById('editStatus-' + idx);
        if (status) { status.textContent = '已还原'; status.style.color = ''; }
    }

    // ── Campaign Profiles ────────────────────────────────────────
    const _PROFILES = {{ profiles | tojson }};

    function loadProfile(idx) {
        if (idx === '') return;
        const p = _PROFILES[parseInt(idx)];
        if (!p) return;
        const form = document.getElementById('configForm');
        const setSelect = (name, val) => {
            const el = form ? form.querySelector('select[name="' + name + '"]')
                            : document.querySelector('select[name="' + name + '"]');
            if (el) el.value = val;
        };
        setSelect('platform', p.platform || 'blogger');
        setSelect('target_language', p.language || 'zh-CN');
        setSelect('url_mode', p.url_mode || 'A');
        setSelect('publish_mode', p.publish_mode || 'draft');
        const picker = document.getElementById('profilePicker');
        if (picker) picker.value = '';
    }

    function loadBatchProfile(idx) {
        if (idx === '') return;
        const p = _PROFILES[parseInt(idx)];
        if (!p) return;
        const setSelect = (id, val) => {
            const el = document.querySelector('#batchForm select[name="' + id + '"]');
            if (el) el.value = val;
        };
        setSelect('platform', p.platform || 'blogger');
        setSelect('language', p.language || 'zh-CN');
        setSelect('url_mode', p.url_mode || 'A');
        setSelect('publish_mode', p.publish_mode || 'draft');
    }

    function saveProfilePrompt() {
        const name = prompt('配置名称（如：51acgs-zh-blogger）：', '');
        if (!name || !name.trim()) return;
        const getVal = (sel) => {
            const el = document.querySelector('select[name="' + sel + '"]');
            return el ? el.value : '';
        };
        const data = new FormData();
        data.append('profile_name', name.trim());
        data.append('platform', getVal('platform'));
        data.append('language', getVal('target_language'));
        data.append('url_mode', getVal('url_mode'));
        data.append('publish_mode', getVal('publish_mode'));
        fetch('/profiles/save', { method: 'POST', body: data })
            .then(r => r.json())
            .then(d => {
                if (d.ok) alert('配置「' + name.trim() + '」已保存 ✓');
                else alert('保存失败：' + (d.error || '未知错误'));
            });
    }

    // ── Loading Overlay ──────────────────────────────────────────
    (function() {
        const MSGS = {
            '/ce:plan':         { text: '分析网址中…',     sub: '正在抓取页面元数据' },
            '/ce:generate':     { text: 'AI 生成文章中…', sub: '调用 AI 生成外链文章，约需 30–60 秒' },
            '/ce:validate':     { text: '验证内容中…',     sub: '检查外链格式与内容合规性' },
            '/ce:publish':      { text: '发布中…',         sub: '正在发布到目标平台，请勿关闭页面' },
            '/ce:publish-real': { text: '正式发布中…',     sub: '正在写入平台，请勿关闭页面' },
        '/ce:batch':        { text: '批量发布中…',     sub: '正在逐篇生成并发布，每篇约 30–60 秒，请勿关闭页面' },
        };

        document.addEventListener('submit', function(e) {
            const form = e.target;
            const action = (form.getAttribute('action') || '').split('?')[0];
            if (['/ce:clear','/ce:history/delete','/ce:history/update-status'].includes(action)) return;

            const msg = MSGS[action] || { text: '处理中…', sub: '请稍候' };
            document.getElementById('_loadingText').textContent    = msg.text;
            document.getElementById('_loadingSubtext').textContent = msg.sub;
            document.getElementById('_loadingOverlay').style.display = 'flex';

            form.querySelectorAll('[type="submit"]').forEach(function(btn) {
                btn.disabled = true;
            });
        });
    })();
    </script>

    <!-- Loading Overlay -->
    <div id="_loadingOverlay" style="display:none;position:fixed;inset:0;background:rgba(15,15,15,0.55);z-index:9999;flex-direction:column;align-items:center;justify-content:center;">
        <div style="background:white;border-radius:20px;padding:40px 48px;text-align:center;max-width:320px;box-shadow:0 24px 64px rgba(0,0,0,0.25);">
            <div class="spinner-border mb-4" style="width:3rem;height:3rem;color:var(--primary);" role="status">
                <span class="visually-hidden">Loading…</span>
            </div>
            <div id="_loadingText" style="font-size:1.1rem;font-weight:700;color:#1f2937;margin-bottom:8px;">处理中…</div>
            <div id="_loadingSubtext" style="font-size:0.85rem;color:#6b7280;line-height:1.5;">请稍候</div>
        </div>
    </div>
</body>
</html>
'''


def _get_blogger_token_status() -> dict:
    """Return token health status without making network calls.

    Returns a dict with:
      state: 'ok' | 'expiring' | 'expired' | 'none'
      label: human-readable string
      days_left: int or None
    """
    try:
        from backlink_publisher.config import load_config as _load_cfg, load_blogger_token as _load_tok
        cfg = _load_cfg()
        token_data = _load_tok(cfg.blogger_token_path)
        if not token_data:
            return {'state': 'none', 'label': '未授权', 'days_left': None}

        if not cfg.blogger_oauth:
            return {'state': 'none', 'label': '未配置 OAuth', 'days_left': None}

        from google.oauth2.credentials import Credentials
        try:
            creds = Credentials.from_authorized_user_info(token_data, ['https://www.googleapis.com/auth/blogger'])
        except Exception:
            return {'state': 'expired', 'label': 'Token 无效', 'days_left': 0}

        if creds.expiry is None:
            # No expiry stored — freshly issued token, assumed valid
            return {'state': 'ok', 'label': 'Token 有效', 'days_left': None}

        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        expiry = creds.expiry
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        delta = expiry - now
        days = delta.days

        if days < 0:
            if creds.refresh_token:
                return {'state': 'expiring', 'label': 'Token 已过期（将自动刷新）', 'days_left': days}
            return {'state': 'expired', 'label': 'Token 已过期，需重新授权', 'days_left': days}
        if days <= 3:
            return {'state': 'expiring', 'label': f'Token {days} 天后到期', 'days_left': days}
        return {'state': 'ok', 'label': f'Token 有效（{days} 天）', 'days_left': days}
    except Exception:
        return {'state': 'ok', 'label': 'Blogger 已连接', 'days_left': None}


def fetch_url_metadata(url):
    """Fetch metadata from a URL"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        }
        resp = requests.get(url, headers=headers, timeout=10, verify=False)
        resp.raise_for_status()
        
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        title = ''
        og_title = soup.find('meta', property='og:title')
        if og_title:
            title = og_title.get('content', '')
        if not title:
            title = soup.find('title')
            title = title.text if title else ''
        
        desc = ''
        og_desc = soup.find('meta', property='og:description')
        if og_desc:
            desc = og_desc.get('content', '')
        if not desc:
            meta_desc = soup.find('meta', attrs={'name': 'description'})
            if meta_desc:
                desc = meta_desc.get('content', '')
        
        return {
            'url': url,
            'title': title.strip() if title else '',
            'description': desc.strip() if desc else '',
            'status': 'success'
        }
    except Exception as e:
        return {
            'url': url,
            'title': '',
            'description': '',
            'status': 'error',
            'error': str(e)
        }

def fetch_full_tdk(url):
    """Fetch full TDK (Title, Description, Keywords) from URL"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        }
        resp = requests.get(url, headers=headers, timeout=15, verify=False)
        resp.raise_for_status()
        
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        title = ''
        og_title = soup.find('meta', property='og:title')
        if og_title:
            title = og_title.get('content', '')
        if not title:
            title_tag = soup.find('title')
            title = title_tag.text if title_tag else ''
        
        description = ''
        og_desc = soup.find('meta', property='og:description')
        if og_desc:
            description = og_desc.get('content', '')
        if not description:
            meta_desc = soup.find('meta', attrs={'name': 'description'})
            if meta_desc:
                description = meta_desc.get('content', '')
        
        keywords = ''
        meta_keywords = soup.find('meta', attrs={'name': 'keywords'})
        if meta_keywords:
            keywords = meta_keywords.get('content', '')
        
        title = title.strip() if title else ''
        description = description.strip() if description else ''
        keywords = keywords.strip() if keywords else ''
        
        system_prompt = f"""你是一个专业的网站内容作家。请根据以下目标网站的SEO信息，创作一篇高质量的反向链接文章。

目标网站信息:
- 标题: {title}
- 描述: {description}
- 关键词: {keywords}

文章要求:
1. 内容要与目标网站主题相关
2. 自然地嵌入目标网站链接
3. 保持专业、流畅的写作风格
4. 字数控制在100-200字之间

请生成一篇有价值的文章内容。"""
        
        return {
            'title': title,
            'description': description,
            'keywords': keywords,
            'system_prompt': system_prompt,
            'status': 'success'
        }
    except Exception as e:
        return {
            'title': '',
            'description': '',
            'keywords': '',
            'system_prompt': '',
            'status': 'error',
            'error': str(e)
        }

def detect_platform(url):
    """Auto-detect platform from URL"""
    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    
    if 'medium.com' in domain:
        return 'medium'
    elif 'blogspot.com' in domain or 'blogger.com' in domain:
        return 'blogger'
    elif 'wordpress.com' in domain:
        return 'wordpress'
    else:
        return 'medium'

def detect_language(url):
    """Auto-detect language from URL"""
    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    path = parsed.path.lower()
    
    if '.cn' in domain or 'cn' in path:
        return 'zh-CN'
    elif '.tw' in domain or 'tw' in path or 'hk' in path:
        return 'zh-TW'
    elif '.jp' in domain or 'jp' in path or 'ja' in path:
        return 'ja'
    elif '.ru' in domain or 'ru' in path:
        return 'ru'
    elif '.es' in domain or 'es' in path:
        return 'es'
    elif '.de' in domain or 'de' in path:
        return 'de'
    elif '.fr' in domain or 'fr' in path:
        return 'fr'
    else:
        return 'en'

def get_main_domain(url):
    """Extract main domain from URL"""
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _parse_publish_results(jsonl_str):
    """Parse publish-backlinks JSONL output into a list of result dicts."""
    results = []
    for line in (jsonl_str or '').strip().split('\n'):
        if line.strip():
            try:
                results.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return results


# ── File-based persistent history ────────────────────────────────────────────
_HISTORY_FILE = Path.home() / '.config' / 'backlink-publisher' / 'publish-history.json'


def _load_history():
    if _HISTORY_FILE.exists():
        try:
            return json.loads(_HISTORY_FILE.read_text(encoding='utf-8'))
        except Exception:
            return []
    return []


def _append_history(item: dict) -> list:
    history = _load_history()
    history.insert(0, item)
    history = history[:100]
    _HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    _HISTORY_FILE.write_text(
        json.dumps(history, ensure_ascii=False, indent=2), encoding='utf-8'
    )
    return history


_PROFILES_FILE = Path.home() / '.config' / 'backlink-publisher' / 'campaign-profiles.json'


def _load_profiles() -> list:
    if _PROFILES_FILE.exists():
        try:
            return json.loads(_PROFILES_FILE.read_text(encoding='utf-8'))
        except Exception:
            return []
    return []


def _save_profiles(profiles: list) -> None:
    _PROFILES_FILE.parent.mkdir(parents=True, exist_ok=True)
    _PROFILES_FILE.write_text(
        json.dumps(profiles, ensure_ascii=False, indent=2), encoding='utf-8'
    )


# ── Draft Queue (排程草稿欄) ──────────────────────────────────────────────────
_DRAFT_FILE = Path.home() / '.config' / 'backlink-publisher' / 'draft-queue.json'
_SCHEDULE_SETTINGS_FILE = Path.home() / '.config' / 'backlink-publisher' / 'schedule-settings.json'
_draft_lock = threading.Lock()


def _load_draft_queue() -> list:
    if _DRAFT_FILE.exists():
        try:
            return json.loads(_DRAFT_FILE.read_text(encoding='utf-8'))
        except Exception:
            return []
    return []


def _save_draft_queue(items: list) -> None:
    _DRAFT_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _DRAFT_FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(_DRAFT_FILE)


def _get_draft_item(item_id: str) -> dict | None:
    with _draft_lock:
        for item in _load_draft_queue():
            if item.get('id') == item_id:
                return item
    return None


def _update_draft_item(item_id: str, **fields) -> bool:
    with _draft_lock:
        items = _load_draft_queue()
        for item in items:
            if item.get('id') == item_id:
                item.update(fields)
                _save_draft_queue(items)
                return True
    return False


def _delete_draft_item(item_id: str) -> bool:
    with _draft_lock:
        items = _load_draft_queue()
        new_items = [i for i in items if i.get('id') != item_id]
        if len(new_items) < len(items):
            _save_draft_queue(new_items)
            return True
    return False


def _load_schedule_settings() -> dict:
    defaults = {'min_interval_hours': 4, 'jitter_minutes': 30}
    if _SCHEDULE_SETTINGS_FILE.exists():
        try:
            data = json.loads(_SCHEDULE_SETTINGS_FILE.read_text(encoding='utf-8'))
            defaults.update(data)
        except Exception:
            pass
    return defaults


def _save_schedule_settings(data: dict) -> None:
    _SCHEDULE_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _SCHEDULE_SETTINGS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8'
    )


def _calc_next_available(requested_dt: datetime) -> datetime:
    """Return the earliest publish time that respects the min-interval + jitter setting."""
    settings = _load_schedule_settings()
    min_hours = settings.get('min_interval_hours', 4)
    jitter_mins = settings.get('jitter_minutes', 30)

    # Find the latest scheduled/published time across queue and history
    last_dt = None
    for item in _load_draft_queue():
        ts = item.get('scheduled_at')
        if ts:
            try:
                dt = datetime.fromisoformat(ts)
                if last_dt is None or dt > last_dt:
                    last_dt = dt
            except ValueError:
                pass
    for item in _load_history():
        ts = item.get('created_at')
        if ts and item.get('status') in ('published', 'success', 'drafted'):
            try:
                dt = datetime.strptime(ts, '%Y-%m-%d %H:%M')
                if last_dt is None or dt > last_dt:
                    last_dt = dt
            except ValueError:
                pass

    if last_dt is None:
        earliest = requested_dt
    else:
        jitter = random.randint(-jitter_mins, jitter_mins)
        earliest = last_dt + timedelta(hours=min_hours) + timedelta(minutes=jitter)

    return max(requested_dt, earliest)


def _load_incomplete_run():
    """Return the most recent incomplete checkpoint run (with pending_count), or None."""
    try:
        runs = _checkpoint_mod.list_incomplete()
    except Exception:
        return None
    if not runs:
        return None
    run = runs[0]
    pending_count = sum(1 for i in run.get("items", []) if i.get("status") in ("pending", "failed"))
    return {**run, "pending_count": pending_count}


def _render(template, **kwargs):
    """Render a template, auto-injecting history and token status when not provided."""
    if 'history' not in kwargs:
        kwargs['history'] = _load_history()
    if 'blogger_token_status' not in kwargs:
        kwargs['blogger_token_status'] = _get_blogger_token_status()
    if 'profiles' not in kwargs:
        kwargs['profiles'] = _load_profiles()
    if 'draft_queue' not in kwargs:
        kwargs['draft_queue'] = _load_draft_queue()
    if 'now_iso' not in kwargs:
        now = datetime.now()
        kwargs['now_iso'] = now.strftime('%Y-%m-%dT%H:%M')
        kwargs.setdefault('suggested_next',
                          _calc_next_available(now + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M'))
    if 'incomplete_run' not in kwargs:
        kwargs['incomplete_run'] = _load_incomplete_run()
    return render_template_string(template, **kwargs)


def _publish_draft_job(item_id: str) -> None:
    """APScheduler job: publish a draft item and update history."""
    item = _get_draft_item(item_id)
    if not item or item.get('status') != 'scheduled':
        return

    platform = item.get('platform', 'medium')
    publish_mode = item.get('publish_mode', 'draft')
    plans_jsonl = item.get('plans_jsonl', '')

    try:
        cmd = ['publish-backlinks', '--platform', platform, '--mode', publish_mode]
        result = run_pipe(cmd, plans_jsonl)
        published = result['stdout']

        if not published.strip():
            raise RuntimeError(result.get('stderr') or '发布失败，无输出')

        publish_results = _parse_publish_results(published)
        article_urls = [r.get('published_url') or r.get('draft_url', '')
                        for r in publish_results if r]
        article_urls = [u for u in article_urls if u]

        _update_draft_item(item_id, status='published',
                           article_urls=article_urls,
                           published_at=datetime.now().strftime('%Y-%m-%d %H:%M'))
        _append_history({
            'id': str(uuid.uuid4())[:8],
            'target_url': item.get('target_url', 'unknown'),
            'platform': platform,
            'language': item.get('language', 'zh-CN'),
            'status': 'drafted' if publish_mode == 'draft' else 'published',
            'created_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
            'article_urls': article_urls,
        })
    except Exception as exc:
        _update_draft_item(item_id, status='failed', error=str(exc))
        _append_history({
            'id': str(uuid.uuid4())[:8],
            'target_url': item.get('target_url', 'unknown'),
            'platform': platform,
            'language': item.get('language', 'zh-CN'),
            'status': 'failed',
            'created_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
            'article_urls': [],
            'error': str(exc),
        })


def _restore_scheduled_jobs() -> None:
    """On startup, re-register any 'scheduled' draft items into APScheduler."""
    now = datetime.now()
    for item in _load_draft_queue():
        if item.get('status') != 'scheduled':
            continue
        item_id = item.get('id')
        ts = item.get('scheduled_at')
        if not item_id or not ts:
            continue
        try:
            run_date = datetime.fromisoformat(ts)
            if run_date < now:
                run_date = now + timedelta(seconds=5)
            _scheduler.add_job(
                _publish_draft_job,
                trigger='date',
                run_date=run_date,
                id=item_id,
                args=[item_id],
                replace_existing=True,
            )
        except Exception:
            pass


@app.route('/')
def index():
    config = session.get('config', {})
    ready_to_publish = None
    validated = session.get('validated', '')
    if validated:
        ready_to_publish = {
            'data': validated,
            'platform': config.get('platform', 'medium')
        }
    tab = request.args.get('tab', '')
    flash_type = request.args.get('flash_type', '')
    flash_msg = request.args.get('flash_msg', '')
    flash = {'type': flash_type, 'msg': flash_msg} if flash_type else None
    history_active = tab == 'draft'
    return _render(HTML, config=config, ready_to_publish=ready_to_publish,
                   history_active=history_active, flash=flash)

@app.route('/ce:clear', methods=['POST'])
def ce_clear():
    """Clear session and restart"""
    session.clear()
    return _render(HTML)

@app.route('/ce:plan', methods=['POST'])
def ce_plan():
    url_inputs = []
    for key in request.form.keys():
        if key.startswith('url_'):
            val = request.form.get(key, '').strip()
            if val:
                if not val.startswith(('http://', 'https://')):
                    val = 'https://' + val
                url_inputs.append(val)
        elif key == 'url_new':
            val = request.form.get(key, '').strip()
            if val:
                if not val.startswith(('http://', 'https://')):
                    val = 'https://' + val
                url_inputs.append(val)
    
    target_url = request.form.get('target_url', '').strip()
    if target_url and target_url not in url_inputs:
        if not target_url.startswith(('http://', 'https://')):
            target_url = 'https://' + target_url
        url_inputs = [target_url] + url_inputs
    
    if not url_inputs:
        return _render(HTML, error="请输入至少一个网址")
    
    urls_json = json.dumps(url_inputs)
    
    meta_info = []
    for url in url_inputs[:5]:
        meta = fetch_url_metadata(url)
        if meta['status'] == 'success':
            meta_info.append(meta)
    
    target_url = url_inputs[0]
    target_language = request.form.get('target_language', detect_language(target_url))
    
    config = {
        'target_url': target_url,
        'main_domain': get_main_domain(target_url),
        'platform': detect_platform(target_url),
        'url_mode': 'A',
        'publish_mode': 'draft',
        'target_language': target_language,
        'custom_title': '',
        'custom_tags': '',
        'fetch_tdk': 'yes',
        'urls': url_inputs,
        'meta_info': meta_info
    }
    
    # Store config in session
    session['config'] = config
    session['urls_json'] = urls_json
    
    extra_urls = url_inputs[1:] if len(url_inputs) > 1 else []
    return _render(HTML, 
        target_url=target_url, 
        config=config,
        urls_json=urls_json,
        extra_urls=extra_urls,
        meta_info=meta_info[:3])

@app.route('/ce:generate', methods=['POST'])
def ce_generate():
    # Get config from session or form
    stored_config = session.get('config', {})
    urls_json = request.form.get('urls_json', session.get('urls_json', '[]'))
    
    try:
        urls = json.loads(urls_json)
    except:
        urls = stored_config.get('urls', [])
    
    if not urls:
        return _render(HTML, error="没有有效的连结", config=stored_config)
    
    # Get settings from form or fall back to stored config
    platform = request.form.get('platform', stored_config.get('platform', 'medium'))
    url_mode = request.form.get('url_mode', stored_config.get('url_mode', 'A'))
    publish_mode = request.form.get('publish_mode', stored_config.get('publish_mode', 'draft'))
    target_language = request.form.get('target_language', stored_config.get('target_language', 'zh-CN'))
    custom_title = request.form.get('custom_title', '').strip()
    custom_tags = request.form.get('custom_tags', '').strip()
    fetch_tdk = request.form.get('fetch_tdk', stored_config.get('fetch_tdk', 'no'))
    
    seeds = []
    main_url = urls[0]
    extra_urls = urls[1:] if len(urls) > 1 else []
    
    tdk_data = {}
    if fetch_tdk == 'yes':
        tdk_data = fetch_full_tdk(main_url)
    
    seed = {
        'target_url': main_url,
        'main_domain': get_main_domain(main_url),
        'platform': platform,
        'language': detect_language(main_url),
        'url_mode': url_mode,
        'publish_mode': publish_mode,
        'target_language': target_language,
    }
    
    if custom_title:
        seed['custom_title'] = custom_title
    if custom_tags:
        seed['custom_tags'] = custom_tags
    if extra_urls:
        seed['extra_urls'] = extra_urls
    if tdk_data:
        seed['system_prompt'] = tdk_data.get('system_prompt', '')
        seed['tdk_title'] = tdk_data.get('title', '')
        seed['tdk_description'] = tdk_data.get('description', '')
        seed['tdk_keywords'] = tdk_data.get('keywords', '')
    
    seeds.append(seed)
    
    seed_json = json.dumps(seed, ensure_ascii=False)
    
    try:
        result = run_pipe(['plan-backlinks'], seed_json)
        plans = result['stdout']
        
        if not plans.strip():
            error_msg = result['stderr'] if result['stderr'] else "生成失败，没有输出"
            return _render(HTML, target_url=main_url, error=error_msg)
        
        plans_list = []
        try:
            for line in plans.strip().split('\n'):
                if line.strip():
                    try:
                        plans_list.append(json.loads(line))
                    except json.JSONDecodeError as je:
                        print(f"JSON parse error: {je}, line: {line[:100]}", file=sys.stderr)
        except Exception as pe:
            plans_list = []
        
        if not plans_list:
            return _render(HTML, target_url=main_url, 
                error=f"解析生成结果失败。原始输出: {plans[:200]}")
        
        # Update session config
        config = {
            'platform': platform,
            'target_language': target_language,
            'urls': urls,
            'fetch_tdk': fetch_tdk,
            'url_mode': url_mode,
            'publish_mode': publish_mode,
            'custom_title': custom_title,
            'custom_tags': custom_tags,
        }
        session['config'] = config
        session['plans'] = plans
        
        return _render(HTML,
            target_url=main_url,
            config=config,
            plans=plans,
            plans_list=plans_list,
            urls_json=urls_json,
            extra_urls=extra_urls)
    except Exception as e:
        return _render(HTML, target_url=main_url, error=str(e), config=stored_config)

@app.route('/ce:validate', methods=['POST'])
def ce_validate():
    plans = session.get('plans', '') or request.form.get('plans', '')
    config = session.get('config', {})
    
    try:
        result = run_pipe(['validate-backlinks', '--no-check-urls'], plans)
        validated = result['stdout']
        
        if not validated.strip():
            error_msg = result['stderr'] if result['stderr'] else "验证失败，请检查链接数量是否在 6-8 个之间"
            return _render(HTML, plans=plans, error=error_msg, config=config)
        
        session['validated'] = validated

        return _render(HTML, validated=validated, plans=plans,
            config=config)
    except Exception as e:
        return _render(HTML, plans=plans, error=str(e), config=config)

@app.route('/ce:publish', methods=['POST'])
def ce_publish():
    plans = session.get('plans', '') or request.form.get('plans', '')
    config = session.get('config', {})
    
    # Get platform from config or form
    platform = request.form.get('platform', config.get('platform', 'medium'))
    
    # Fallback: get platform from first plan if empty
    if not platform or platform == 'None':
        try:
            for line in plans.strip().split('\n'):
                if line.strip():
                    first_plan = json.loads(line)
                    platform = first_plan.get('platform', 'medium')
                    break
        except:
            platform = 'medium'
    
    publish_mode = request.form.get('publish_mode', 'draft')  # 'draft' or 'publish'

    # Pre-flight: Blogger requires blog_id mapping
    if platform == 'blogger':
        try:
            from backlink_publisher.config import load_config as _load_cfg, resolve_blog_id as _resolve
            _cfg = _load_cfg()
            _main_domain = config.get('main_domain', '')
            if _main_domain:
                _resolve(_cfg, _main_domain)
        except Exception as _pre_err:
            err_str = str(_pre_err)
            if 'blog_id' in err_str or 'blog_id' in err_str.lower() or 'DependencyError' in type(_pre_err).__name__:
                friendly = (
                    f"❌ Blogger Blog ID 未配置：域名 <code>{config.get('main_domain', '?')}</code> "
                    f"尚未绑定 Blog ID。<br><br>"
                    f"请前往 <a href='/settings#blogger-blog-ids' style='color:var(--primary);font-weight:600;'>"
                    f"设置 → Blogger Blog ID 映射</a> 添加对应条目。<br><br>"
                    f"Blog ID 可在 Blogger 控制台 URL 中找到：<br>"
                    f"<code>https://www.blogger.com/blog/posts/<strong>&lt;数字ID&gt;</strong></code>"
                )
                return _render(HTML, plans=plans, error=friendly, config=config)

    cmd = ['publish-backlinks', '--platform', platform, '--mode', publish_mode]

    try:
        result = run_pipe(cmd, plans)
        published = result['stdout']

        if not published.strip():
            error_msg = result['stderr'] if result['stderr'] else "发布失败"
            return _render(HTML, plans=plans, error=error_msg, config=config)

        publish_results = _parse_publish_results(published)
        article_urls = [r.get('published_url') or r.get('draft_url', '')
                        for r in publish_results if r]
        _append_history({
            'id': str(uuid.uuid4())[:8],
            'target_url': config.get('target_url', 'unknown'),
            'platform': platform,
            'language': config.get('target_language', 'zh-CN'),
            'status': 'drafted' if publish_mode == 'draft' else 'published',
            'created_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
            'article_urls': [u for u in article_urls if u],
        })
        return _render(HTML, plans=plans, published=published,
            publish_results=publish_results,
            validated=plans, config=config)
    except Exception as e:
        _append_history({
            'id': str(uuid.uuid4())[:8],
            'target_url': config.get('target_url', 'unknown'),
            'platform': platform,
            'language': config.get('target_language', 'zh-CN'),
            'status': 'failed',
            'created_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
            'article_urls': [],
            'error': str(e),
        })
        return _render(HTML, plans=plans, error=str(e), config=config)

def _draft_tab_extra() -> dict:
    """Extra template vars for the draft queue tab."""
    now = datetime.now()
    suggested = _calc_next_available(now + timedelta(hours=1))
    return {
        'now_iso': now.strftime('%Y-%m-%dT%H:%M'),
        'suggested_next': suggested.strftime('%Y-%m-%dT%H:%M'),
    }


@app.route('/ce:history', methods=['GET', 'POST'])
def ce_history():
    """View history or load specific history item"""
    config = session.get('config', {})
    validated = session.get('validated', '')
    ready_to_publish = (
        {'data': validated, 'platform': config.get('platform', 'medium')}
        if validated else None
    )
    return _render(HTML,
        history=_load_history(),
        history_active=True,
        ready_to_publish=ready_to_publish,
        config=config,
        **_draft_tab_extra())


@app.route('/ce:history/delete', methods=['POST'])
def ce_history_delete():
    """Delete one history record by id."""
    item_id = request.form.get('id', '')
    history = _load_history()
    history = [h for h in history if h.get('id') != item_id]
    _HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    _HISTORY_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding='utf-8')
    return _render(HTML, history=history, history_active=True,
                   config=session.get('config', {}))


@app.route('/ce:history/update-status', methods=['POST'])
def ce_history_update_status():
    """Update the status of one history record."""
    item_id = request.form.get('id', '')
    new_status = request.form.get('status', '')
    history = _load_history()
    for h in history:
        if h.get('id') == item_id:
            h['status'] = new_status
            break
    _HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    _HISTORY_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding='utf-8')
    return _render(HTML, history=history, history_active=True,
                   config=session.get('config', {}))


@app.route('/ce:history/reuse', methods=['POST'])
def ce_history_reuse():
    """Load a history target_url back into the main task form."""
    target_url = request.form.get('target_url', '')
    session.pop('plans', None)
    session.pop('validated', None)
    # Pre-fill the URL for the user
    return _render(HTML, target_url=target_url, config=session.get('config', {}))


# ── Draft Queue Routes ────────────────────────────────────────────────────────

@app.route('/ce:draft/save', methods=['POST'])
def ce_draft_save():
    """Save current validated plans as a draft queue item."""
    plans_jsonl = request.form.get('plans', '').strip()
    if not plans_jsonl:
        return redirect('/?tab=draft&flash_type=danger&flash_msg=没有可保存的内容')
    config = session.get('config', {})
    platform = request.form.get('platform', config.get('platform', 'medium'))
    publish_mode = request.form.get('publish_mode', 'draft')
    target_url = config.get('target_url', request.form.get('target_url', 'unknown'))
    language = config.get('target_language', 'zh-CN')

    item = {
        'id': str(uuid.uuid4())[:8],
        'target_url': target_url,
        'platform': platform,
        'language': language,
        'publish_mode': publish_mode,
        'plans_jsonl': plans_jsonl,
        'status': 'pending',
        'scheduled_at': None,
        'created_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'article_urls': [],
        'error': None,
    }
    with _draft_lock:
        items = _load_draft_queue()
        items.insert(0, item)
        _save_draft_queue(items)
    return redirect('/?tab=draft&flash_type=success&flash_msg=已加入草稿栏')


@app.route('/ce:draft/schedule', methods=['POST'])
def ce_draft_schedule():
    """Schedule a draft item for publishing at a given datetime."""
    item_id = request.form.get('id', '')
    scheduled_at_str = request.form.get('scheduled_at', '')
    if not item_id or not scheduled_at_str:
        return redirect('/?tab=draft&flash_type=danger&flash_msg=参数缺失')

    try:
        requested_dt = datetime.fromisoformat(scheduled_at_str)
    except ValueError:
        return redirect('/?tab=draft&flash_type=danger&flash_msg=时间格式错误')

    final_dt = _calc_next_available(requested_dt)
    _update_draft_item(item_id, status='scheduled', scheduled_at=final_dt.isoformat())
    _scheduler.add_job(
        _publish_draft_job,
        trigger='date',
        run_date=final_dt,
        id=item_id,
        args=[item_id],
        replace_existing=True,
    )
    adjusted = final_dt != requested_dt
    msg = f'已排程：{final_dt.strftime("%Y-%m-%d %H:%M")}'
    if adjusted:
        msg += '（已依间隔设定自动调整）'
    return redirect(f'/?tab=draft&flash_type=success&flash_msg={msg}')


@app.route('/ce:draft/publish-now', methods=['POST'])
def ce_draft_publish_now():
    """Immediately schedule a draft item to publish in ~5 seconds."""
    item_id = request.form.get('id', '')
    if not item_id:
        return redirect('/?tab=draft&flash_type=danger&flash_msg=参数缺失')
    run_date = datetime.now() + timedelta(seconds=5)
    _update_draft_item(item_id, status='scheduled', scheduled_at=run_date.isoformat())
    _scheduler.add_job(
        _publish_draft_job,
        trigger='date',
        run_date=run_date,
        id=item_id,
        args=[item_id],
        replace_existing=True,
    )
    return redirect('/?tab=draft&flash_type=info&flash_msg=正在发布，请稍候刷新页面')


@app.route('/ce:draft/cancel', methods=['POST'])
def ce_draft_cancel():
    """Cancel a scheduled draft job."""
    item_id = request.form.get('id', '')
    if not item_id:
        return redirect('/?tab=draft&flash_type=danger&flash_msg=参数缺失')
    try:
        _scheduler.remove_job(item_id)
    except Exception:
        pass
    _update_draft_item(item_id, status='pending', scheduled_at=None)
    return redirect('/?tab=draft&flash_type=success&flash_msg=已取消排程')


@app.route('/ce:draft/delete', methods=['POST'])
def ce_draft_delete():
    """Delete a draft item (cancel job if scheduled)."""
    item_id = request.form.get('id', '')
    if not item_id:
        return redirect('/?tab=draft&flash_type=danger&flash_msg=参数缺失')
    try:
        _scheduler.remove_job(item_id)
    except Exception:
        pass
    _delete_draft_item(item_id)
    return redirect('/?tab=draft&flash_type=success&flash_msg=已删除')


@app.route('/settings/save-target-keywords', methods=['POST'])
def settings_save_target_keywords():
    """Save SEO anchor keyword pools for all target domains."""
    try:
        count = int(request.form.get('domain_count', 0))
        new_pools: dict[str, list[str]] = {}
        dup_warnings: list[str] = []

        for i in range(1, count + 1):
            domain = request.form.get(f'domain_{i}', '').strip()
            raw = request.form.get(f'keywords_{i}', '')
            if not domain:
                continue

            lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
            # Validate length ≤ 60
            invalid = [ln for ln in lines if len(ln) > 60]
            if invalid:
                return redirect(
                    f'/settings?flash_type=danger&flash_msg='
                    f'关键词过长（>60字符）: {invalid[0][:30]}…'
                )

            # De-duplicate while preserving order
            seen: set[str] = set()
            deduped: list[str] = []
            for kw in lines:
                if kw in seen:
                    dup_warnings.append(domain)
                else:
                    seen.add(kw)
                    deduped.append(kw)

            # Empty list means "clear this target's pool"
            new_pools[domain] = deduped

        cfg = load_config()
        save_config(cfg, target_anchor_keywords=new_pools)

        msg = '关键词池已保存'
        if dup_warnings:
            msg += f'（自动去重 {len(dup_warnings)} 条）'
        return redirect(f'/settings?flash_type=success&flash_msg={msg}')
    except Exception as e:
        return redirect(f'/settings?flash_type=danger&flash_msg=保存失败: {e}')


@app.route('/settings/schedule', methods=['POST'])
def settings_schedule_save():
    """Save schedule interval settings."""
    try:
        min_hours = float(request.form.get('min_interval_hours', 4))
        jitter_mins = int(request.form.get('jitter_minutes', 30))
        _save_schedule_settings({
            'min_interval_hours': max(0.5, min_hours),
            'jitter_minutes': max(0, jitter_mins),
        })
        return redirect('/settings?flash_type=success&flash_msg=排程设定已保存')
    except Exception as e:
        return redirect(f'/settings?flash_type=danger&flash_msg=保存失败: {e}')


@app.route('/ce:batch', methods=['POST'])
def ce_batch():
    """Batch publish: process multiple target URLs through the full pipeline."""
    urls_text = request.form.get('batch_urls', '').strip()
    platform = request.form.get('platform', 'blogger')
    language = request.form.get('language', 'zh-CN')
    url_mode = request.form.get('url_mode', 'A')
    publish_mode = request.form.get('publish_mode', 'draft')

    urls = [u.strip() for u in urls_text.split('\n') if u.strip()]
    if not urls:
        return _render(HTML, error="请输入至少一个网址", batch_tab=True,
                       batch_urls=urls_text, config={})

    # Pre-flight: Blogger requires blog_id mapping
    if platform == 'blogger':
        try:
            from backlink_publisher.config import load_config as _load_cfg, resolve_blog_id as _resolve
            _cfg = _load_cfg()
            first_domain = get_main_domain(urls[0])
            _resolve(_cfg, first_domain)
        except Exception as _pre_err:
            if 'blog_id' in str(_pre_err).lower() or 'DependencyError' in type(_pre_err).__name__:
                friendly = (
                    f"❌ Blogger Blog ID 未配置。"
                    f"请前往 <a href='/settings#blogger-blog-ids' style='color:var(--primary);font-weight:600;'>"
                    f"设置 → Blogger Blog ID 映射</a> 添加对应条目。"
                )
                return _render(HTML, error=friendly, batch_tab=True,
                               batch_urls=urls_text, config={})

    results = []
    for url in urls:
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url
        try:
            main_domain = get_main_domain(url)
            seed = json.dumps({
                'target_url': url,
                'main_domain': main_domain,
                'platform': platform,
                'language': language,
                'url_mode': url_mode,
                'publish_mode': publish_mode,
            }, ensure_ascii=False)

            plan_res = run_pipe(['plan-backlinks'], seed)
            val_res = run_pipe(['validate-backlinks', '--no-check-urls'], plan_res['stdout'])
            cmd = ['publish-backlinks', '--platform', platform, '--mode', publish_mode]
            pub_res = run_pipe(cmd, val_res['stdout'])

            publish_results = _parse_publish_results(pub_res['stdout'])
            article_url = ''
            title = ''
            if publish_results:
                article_url = publish_results[0].get('published_url') or publish_results[0].get('draft_url', '')
                title = publish_results[0].get('title', '')

            _append_history({
                'id': str(uuid.uuid4())[:8],
                'target_url': url,
                'platform': platform,
                'language': language,
                'status': 'drafted' if publish_mode == 'draft' else 'published',
                'created_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
                'article_urls': [article_url] if article_url else [],
            })
            results.append({'url': url, 'status': 'success',
                            'article_url': article_url, 'title': title})
        except Exception as e:
            results.append({'url': url, 'status': 'failed',
                            'article_url': '', 'title': '', 'error': str(e)})

    return _render(HTML, batch_results=results, batch_tab=True,
                   batch_urls=urls_text, config={})


@app.route('/ce:publish-real', methods=['POST'])
def ce_publish_real():
    """Real publish (mode=publish, not dry-run)"""
    validated = request.form.get('validated', '')
    platform = request.form.get('platform', 'medium')
    config = session.get('config', {})

    # Pre-flight: Blogger requires blog_id mapping
    if platform == 'blogger':
        try:
            from backlink_publisher.config import load_config as _load_cfg, resolve_blog_id as _resolve
            _cfg = _load_cfg()
            _main_domain = config.get('main_domain', '')
            if _main_domain:
                _resolve(_cfg, _main_domain)
        except Exception as _pre_err:
            if 'blog_id' in str(_pre_err).lower() or 'DependencyError' in type(_pre_err).__name__:
                friendly = (
                    f"❌ Blogger Blog ID 未配置：域名 <code>{config.get('main_domain', '?')}</code> "
                    f"尚未绑定 Blog ID。<br><br>"
                    f"请前往 <a href='/settings#blogger-blog-ids' style='color:var(--primary);font-weight:600;'>"
                    f"设置 → Blogger Blog ID 映射</a> 添加对应条目。"
                )
                return _render(HTML, error=friendly, config=config, history_active=True)

    try:
        # Real publish - use mode=publish instead of draft
        cmd = ['publish-backlinks', '--platform', platform, '--mode', 'publish']
        
        result = run_pipe(cmd, validated)
        published = result['stdout']
        
        if not published.strip():
            return _render(HTML, 
                error=result['stderr'] or "发布失败",
                config=config, history_active=True)
        
        publish_results = _parse_publish_results(published)
        article_urls = [r.get('published_url') or r.get('draft_url', '')
                        for r in publish_results if r]
        history = _append_history({
            'id': str(uuid.uuid4())[:8],
            'target_url': config.get('target_url', 'unknown'),
            'platform': platform,
            'language': config.get('target_language', 'zh-CN'),
            'status': 'success',
            'created_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
            'article_urls': [u for u in article_urls if u],
        })

        return _render(HTML,
            published=published,
            publish_results=publish_results,
            config=config,
            history=history,
            history_active=True)

    except Exception as e:
        history = _append_history({
            'id': str(uuid.uuid4())[:8],
            'target_url': config.get('target_url', 'unknown'),
            'platform': platform,
            'language': config.get('target_language', 'zh-CN'),
            'status': 'failed',
            'created_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
            'article_urls': [],
            'error': str(e),
        })
        
        return _render(HTML, 
            error=f"发布失败: {str(e)}",
            config=config, 
            history=history,
            history_active=True)

def run_pipe(cmd, stdin):
    """Run a pipeline command"""
    result = subprocess.run(
        cmd,
        input=stdin,
        capture_output=True,
        text=True,
        cwd=os.path.dirname(os.path.abspath(__file__)) or os.getcwd()
    )
    if result.returncode != 0:
        raise Exception(result.stderr or f"Exit code: {result.returncode}")
    return {'stdout': result.stdout, 'stderr': result.stderr}


# ─────────────────────────────────────────────────────────────────────────────
# Checkpoint routes
# ─────────────────────────────────────────────────────────────────────────────

_RUN_ID_RE = re.compile(r"^\d{8}T\d{6}-[0-9a-f]{8}$")


def _check_localhost():
    if request.remote_addr not in ("127.0.0.1", "::1", "localhost"):
        from flask import abort
        abort(403)


def _validate_webui_run_id(run_id):
    if not run_id or not _RUN_ID_RE.match(run_id):
        from flask import abort
        abort(400)


@app.route("/checkpoint/resume", methods=["POST"])
def checkpoint_resume():
    _check_localhost()
    run_id = request.form.get("run_id", "")
    _validate_webui_run_id(run_id)

    cmd = ["publish-backlinks", "--resume", run_id]
    result = subprocess.run(
        cmd,
        input="",
        capture_output=True,
        text=True,
        cwd=os.path.dirname(os.path.abspath(__file__)) or os.getcwd(),
    )

    publish_results = _parse_publish_results(result.stdout)
    config = session.get("config", {})
    platform = publish_results[0].get("platform", "unknown") if publish_results else "unknown"

    if result.returncode == 0:
        history = _append_history({
            "id": str(uuid.uuid4())[:8],
            "target_url": config.get("target_url", "unknown"),
            "platform": platform,
            "language": config.get("target_language", "zh-CN"),
            "status": "published",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "article_urls": [r.get("published_url") or r.get("draft_url", "") for r in publish_results if r],
        })
        return _render(HTML,
            publish_results=publish_results,
            config=config,
            history=history,
            history_active=True,
            flash={"type": "success", "msg": f"恢复发布成功，共 {len(publish_results)} 篇"},
        )
    elif result.returncode == 4:
        done = [r for r in publish_results if r.get("error") is None]
        _append_history({
            "id": str(uuid.uuid4())[:8],
            "target_url": config.get("target_url", "unknown"),
            "platform": platform,
            "language": config.get("target_language", "zh-CN"),
            "status": "failed_partial",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "article_urls": [r.get("published_url") or r.get("draft_url", "") for r in done],
            "stderr_summary": result.stderr[:500] if result.stderr else "",
        })
        return _render(HTML,
            publish_results=publish_results,
            config=config,
            history_active=True,
            error=f"部分发布失败。{result.stderr[:200] if result.stderr else ''}",
        )
    else:
        return _render(HTML,
            config=config,
            error=f"恢复发布失败 (exit {result.returncode}): {result.stderr[:300] if result.stderr else ''}",
        )


@app.route("/checkpoint/dismiss", methods=["POST"])
def checkpoint_dismiss():
    _check_localhost()
    run_id = request.form.get("run_id", "")
    _validate_webui_run_id(run_id)
    try:
        _checkpoint_mod.delete(run_id)
    except Exception:
        pass
    return redirect("/")


# ─────────────────────────────────────────────────────────────────────────────
# Settings routes
# ─────────────────────────────────────────────────────────────────────────────

_FLASK_PORT = int(os.environ.get('PORT', 8888))


def _settings_context(flash=None):
    """Build template context for the settings page."""
    from backlink_publisher.config import load_medium_token
    
    cfg = load_config()
    token_data = load_blogger_token(cfg.blogger_token_path)
    medium_token_data = load_medium_token()

    token = cfg.medium_integration_token or ""
    masked = ("*" * 8 + token[-4:]) if len(token) > 4 else ("*" * len(token))

    # Build the union of known target domains: [blogger] map + [targets] map
    all_targets = sorted(
        set(cfg.blogger_blog_ids.keys()) | set(cfg.target_anchor_keywords.keys())
    )

    return dict(
        flash=flash,
        blogger_token=bool(token_data),
        blogger_client_id=cfg.blogger_oauth.client_id if cfg.blogger_oauth else "",
        blogger_client_secret=cfg.blogger_oauth.client_secret if cfg.blogger_oauth else "",
        blog_ids=cfg.blogger_blog_ids,
        medium_token_set=bool(token),
        medium_token_masked=masked if token else "",
        medium_oauth_configured=bool(medium_token_data and cfg.medium_oauth),
        config_path=str(cfg.config_dir / "config.toml"),
        token_path=str(cfg.blogger_token_path),
        port=_FLASK_PORT,
        callback_uri=_oauth_callback_uri(),
        profiles=_load_profiles(),
        plans_list=[],
        schedule_settings=_load_schedule_settings(),
        all_targets=all_targets,
        target_anchor_keywords=cfg.target_anchor_keywords,
    )


@app.route('/settings')
def settings():
    flash_type = request.args.get('flash_type')
    flash_msg  = request.args.get('flash_msg')
    flash = {"type": flash_type, "msg": flash_msg} if flash_type else None
    return render_template_string(SETTINGS_HTML, **_settings_context(flash=flash))



@app.route('/profiles/save', methods=['POST'])
def profiles_save():
    """Save a campaign profile (AJAX JSON)."""
    name = request.form.get('profile_name', '').strip()
    if not name:
        return jsonify({'ok': False, 'error': '名称不能为空'})
    profiles = _load_profiles()
    # Update if name exists, otherwise append
    for p in profiles:
        if p.get('name') == name:
            p.update({
                'platform': request.form.get('platform', 'blogger'),
                'language': request.form.get('language', 'zh-CN'),
                'url_mode': request.form.get('url_mode', 'A'),
                'publish_mode': request.form.get('publish_mode', 'draft'),
            })
            _save_profiles(profiles)
            return jsonify({'ok': True})
    profiles.append({
        'name': name,
        'platform': request.form.get('platform', 'blogger'),
        'language': request.form.get('language', 'zh-CN'),
        'url_mode': request.form.get('url_mode', 'A'),
        'publish_mode': request.form.get('publish_mode', 'draft'),
    })
    _save_profiles(profiles)
    return jsonify({'ok': True})


@app.route('/profiles/delete', methods=['POST'])
def profiles_delete():
    """Delete a campaign profile by name."""
    name = request.form.get('profile_name', '').strip()
    profiles = [p for p in _load_profiles() if p.get('name') != name]
    _save_profiles(profiles)
    return redirect(request.referrer or '/')


@app.route('/settings/save-blog-ids', methods=['POST'])
def settings_save_blog_ids():
    domains  = request.form.getlist('domain[]')
    blog_ids_list = request.form.getlist('blog_id[]')
    mapping  = {d.strip(): b.strip() for d, b in zip(domains, blog_ids_list)
                if d.strip() and b.strip()}
    try:
        # Load existing config to preserve other settings (OAuth, Medium token)
        cfg = load_config()
        # Override blog_ids completely (not merge) by setting them before calling save
        cfg.blogger_blog_ids = mapping
        save_config(cfg, extra_blogger_ids={})  # extra_blogger_ids={} means no extra additions
        return redirect('/settings?flash_type=success&flash_msg=Blog ID 映射已保存')
    except Exception as e:
        return redirect(f'/settings?flash_type=danger&flash_msg=保存失败: {e}')


@app.route('/settings/save-medium-token', methods=['POST'])
def settings_save_medium_token():
    token = request.form.get('medium_token', '').strip()
    try:
        save_config(load_config(), medium_token=token)
        msg = 'Medium Token 已保存' if token else 'Medium Token 已清除'
        return redirect(f'/settings?flash_type=success&flash_msg={msg}')
    except Exception as e:
        return redirect(f'/settings?flash_type=danger&flash_msg=保存失败: {e}')


@app.route('/settings/clear-medium-token', methods=['POST'])
def settings_clear_medium_token():
    try:
        save_config(load_config(), medium_token="")
        return redirect('/settings?flash_type=success&flash_msg=Medium Token 已清除')
    except Exception as e:
        return redirect(f'/settings?flash_type=danger&flash_msg=清除失败: {e}')


@app.route('/settings/medium/oauth-start', methods=['POST'])
def settings_medium_oauth_start():
    """Save credentials, generate Medium auth URL, redirect user's browser there."""
    import secrets
    
    client_id     = request.form.get('client_id', '').strip()
    client_secret = request.form.get('client_secret', '').strip()
    
    if not client_id or not client_secret:
        return redirect('/settings?flash_type=warning&flash_msg='
                        + '请填写 Client ID 和 Client Secret')
    
    try:
        cfg = load_config()
        # 保存客户端凭据
        from backlink_publisher.config import save_config, MediumOAuthConfig
        cfg.medium_oauth = MediumOAuthConfig(client_id=client_id, client_secret=client_secret)
        # 这里只是临时保存到内存，真实实现需要更新配置文件
        session['medium_client_id'] = client_id
        session['medium_client_secret'] = client_secret
    except Exception as e:
        return redirect(f'/settings?flash_type=danger&flash_msg=凭据保存失败: {e}')
    
    # 生成 OAuth 授权 URL
    state = secrets.token_urlsafe(32)
    session['medium_oauth_state'] = state

    redirect_uri = _oauth_callback_uri().replace('/blogger/oauth-callback', '/medium/oauth-callback')
    oauth_params = {
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'response_type': 'code',
        'state': state,
        'scope': 'basicProfile,publishPost'
    }
    auth_url = f"https://medium.com/m/oauth/authorize?{urlencode(oauth_params)}"

    return redirect(auth_url)


@app.route('/settings/medium/oauth-callback')
def settings_medium_oauth_callback():
    """Medium redirects here after user approves."""
    import requests as req

    err = request.args.get('error')
    if err:
        # Whitelist known OAuth errors and map to safe messages
        SAFE_ERROR_MESSAGES = {
            'access_denied': '用户拒绝了授权',
            'invalid_scope': '请求的权限无效',
            'invalid_request': '授权请求参数有误',
            'server_error': 'Medium 服务器出错，请稍后重试',
            'temporarily_unavailable': 'Medium 服务暂时不可用，请稍后重试'
        }
        error_msg = SAFE_ERROR_MESSAGES.get(err, '授权失败，请重试')
        return redirect(f'/settings?flash_type=danger&flash_msg={error_msg}')
    
    state = session.get('medium_oauth_state')
    code = request.args.get('code')
    client_id = session.get('medium_client_id')
    client_secret = session.get('medium_client_secret')
    
    if not state or not code or not client_id or not client_secret:
        return redirect('/settings?flash_type=warning&flash_msg='
                        + '授权会话已过期，请重新点击授权按钮')
    
    if request.args.get('state') != state:
        return redirect('/settings?flash_type=danger&flash_msg='
                        + 'OAuth state 不匹配（可能是 CSRF 攻击）')
    
    # 用授权码交换 Access Token
    redirect_uri = _oauth_callback_uri().replace('/blogger/oauth-callback', '/medium/oauth-callback')
    try:
        token_resp = req.post(
            "https://api.medium.com/v1/tokens",
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
            timeout=30
        )
        
        if token_resp.status_code != 200:
            raise Exception(f"Token exchange failed with status {token_resp.status_code}")
        
        token_data = token_resp.json()
        access_token = token_data.get("access_token")
        
        if not access_token:
            raise Exception("Missing access_token in Medium response")
        
        # Augment token with expires_at (Unix timestamp) so the adapter can
        # detect near-expiry before making API calls.
        if "expires_in" in token_data and "expires_at" not in token_data:
            token_data["expires_at"] = (
                int(datetime.now(timezone.utc).timestamp()) + int(token_data["expires_in"])
            )

        # 保存 token 和凭据
        from backlink_publisher.config import save_medium_token, MediumOAuthConfig, save_config
        save_medium_token(token_data)

        cfg = load_config()
        cfg.medium_oauth = MediumOAuthConfig(client_id=client_id, client_secret=client_secret)
        save_config(cfg)

        # 清除 session 中的临时数据
        session.pop('medium_oauth_state', None)
        session.pop('medium_client_id', None)
        session.pop('medium_client_secret', None)
        
        return redirect('/settings?flash_type=success&flash_msg=Medium OAuth 授权成功！')
    
    except Exception as e:
        return redirect(f'/settings?flash_type=danger&flash_msg=获取 Token 失败，请检查凭证并重试')


@app.route('/settings/clear-medium-oauth', methods=['POST'])
def settings_clear_medium_oauth():
    """Clear Medium OAuth configuration."""
    from pathlib import Path
    import os
    
    try:
        # 删除 token 文件
        from backlink_publisher.config import _config_dir
        token_file = _config_dir() / "medium-token.json"
        if token_file.exists():
            os.remove(token_file)
        
        return redirect('/settings?flash_type=success&flash_msg=Medium OAuth 授权已清除')
    except Exception as e:
        return redirect(f'/settings?flash_type=danger&flash_msg=清除失败: {e}')


@app.route('/settings/revoke-blogger', methods=['POST'])
def settings_revoke_blogger():
    cfg = load_config()
    try:
        cfg.blogger_token_path.unlink(missing_ok=True)
        return redirect('/settings?flash_type=success&flash_msg=Blogger 授权已撤销')
    except Exception as e:
        return redirect(f'/settings?flash_type=danger&flash_msg=撤销失败: {e}')


@app.route('/settings/save-blogger-oauth', methods=['POST'])
def settings_save_blogger_oauth():
    """Save Client ID / Secret only — no OAuth redirect."""
    client_id     = request.form.get('client_id', '').strip()
    client_secret = request.form.get('client_secret', '').strip()
    if not client_id or not client_secret:
        return redirect('/settings?flash_type=warning&flash_msg=请填写 Client ID 和 Client Secret')
    try:
        save_config(load_config(),
                    blogger_client_id=client_id,
                    blogger_client_secret=client_secret)
        return redirect('/settings?flash_type=success&flash_msg=凭据已确认绑定，可随时点击「使用 Google 帐号登入」完成授权')
    except Exception as e:
        return redirect(f'/settings?flash_type=danger&flash_msg=保存失败: {e}')


def _oauth_callback_uri():
    return f'http://localhost:{_FLASK_PORT}/settings/blogger/oauth-callback'


@app.route('/settings/blogger/oauth-start', methods=['POST'])
def settings_blogger_oauth_start():
    """Save credentials, generate Google auth URL, redirect user's browser there."""
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

    client_id     = request.form.get('client_id', '').strip()
    client_secret = request.form.get('client_secret', '').strip()

    if not client_id or not client_secret:
        return redirect('/settings?flash_type=warning&flash_msg='
                        + '请填写 Client ID 和 Client Secret 后再登入')

    try:
        save_config(load_config(),
                    blogger_client_id=client_id,
                    blogger_client_secret=client_secret)
    except Exception as e:
        return redirect(f'/settings?flash_type=danger&flash_msg=凭据保存失败: {e}')

    from google_auth_oauthlib.flow import Flow
    from backlink_publisher.adapters.blogger_api import _SCOPES

    cb_uri = _oauth_callback_uri()
    # Use 'installed' (Desktop app) type: Google accepts any http://localhost:PORT/PATH
    # as long as http://localhost is registered in Cloud Console — no exact-port match needed.
    # This also works for Web application type clients when the full URI is registered.
    client_config = {
        'installed': {
            'client_id':     client_id,
            'client_secret': client_secret,
            'redirect_uris': ['http://localhost', cb_uri],
            'auth_uri':      'https://accounts.google.com/o/oauth2/auth',
            'token_uri':     'https://oauth2.googleapis.com/token',
        }
    }

    flow = Flow.from_client_config(client_config, scopes=_SCOPES, redirect_uri=cb_uri)
    auth_url, state = flow.authorization_url(access_type='offline', prompt='consent')

    session['oauth_state']         = state
    session['oauth_client_config'] = client_config
    # Save PKCE code_verifier: google_auth_oauthlib may auto-generate it for
    # 'installed' type; Google requires the matching verifier in token exchange.
    session['oauth_code_verifier'] = getattr(flow, 'code_verifier', None)
    return redirect(auth_url)


@app.route('/settings/blogger/oauth-callback')
def settings_blogger_oauth_callback():
    """Google redirects here after the user approves."""
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

    err = request.args.get('error')
    if err:
        return redirect(f'/settings?flash_type=danger&flash_msg=Google 拒绝授权: {err}')

    state         = session.get('oauth_state')
    client_config = session.get('oauth_client_config')
    if not state or not client_config:
        return redirect('/settings?flash_type=warning&flash_msg='
                        + '授权会话已过期，请重新点击登入按钮')

    from google_auth_oauthlib.flow import Flow
    from backlink_publisher.adapters.blogger_api import _SCOPES, json_from_creds
    from backlink_publisher.config import save_blogger_token

    cb_uri = _oauth_callback_uri()
    try:
        flow = Flow.from_client_config(
            client_config, scopes=_SCOPES, state=state, redirect_uri=cb_uri)
        # Restore PKCE code_verifier saved during oauth-start so fetch_token
        # can include it in the token exchange request.
        flow.code_verifier = session.pop('oauth_code_verifier', None)
        # request.url may come in as https; oauthlib needs it to match
        auth_response = request.url
        if auth_response.startswith('https://'):
            auth_response = 'http://' + auth_response[8:]
        flow.fetch_token(authorization_response=auth_response)
        creds = flow.credentials
        cfg   = load_config()
        cfg.blogger_token_path.parent.mkdir(parents=True, exist_ok=True)
        save_blogger_token(json_from_creds(creds), cfg.blogger_token_path)
        session.pop('oauth_state', None)
        session.pop('oauth_client_config', None)
        return redirect('/settings?flash_type=success&flash_msg='
                        + 'Google 帐号授权成功！Token 已保存。')
    except Exception as exc:
        return redirect(f'/settings?flash_type=danger&flash_msg=授权处理失败: {exc}')


if __name__ == '__main__':
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    _scheduler.start()
    _restore_scheduled_jobs()

    port = int(os.environ.get('PORT', 8888))
    print(f"Starting Backlink Publisher Web UI...")
    print(f"Open: http://localhost:{port}")
    app.run(host='0.0.0.0', port=port, debug=True, use_reloader=False)