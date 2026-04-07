<div align="center">

<img src="frontend/static/logo.svg" alt="logo" width="128" height="128">

# Kaloscope

_以可视化工作流驱动的媒体库管理与自动追番工具_

[![GPLv3 License](https://img.shields.io/badge/License-GPLv3-BD0000)](LICENSE)
[![xyflow Version](https://img.shields.io/badge/xyflow-v1.5.2-1A192B?logo=xyflow)](https://xyflow.com/)
[![Svelte Version](https://img.shields.io/badge/Svelte-v5.55.1-FF3E00?logo=svelte)](https://svelte.dev/)
[![Sanic Version](https://img.shields.io/badge/Sanic-v25.12.0-FF0D68?logo=sanic)](https://sanic.dev/)
[![Python Version](https://img.shields.io/badge/Python-3.13+-3776AB?logo=python)](https://www.python.org/)

</div>

---

## 项目简介

Kaloscope 是一款基于**可视化工作流引擎**的媒体库管理工具，资源搜索和元数据刮削均通过用户可自定义的工作流实现，而非硬编码逻辑，让你可以对接任意资源站点和元数据来源。

## 免责声明

本项目仅供个人学习与技术交流使用。请勿将本项目用于任何商业目的，亦不得用于传播盗版资源或其他违反法律法规的活动。使用本项目所引发的一切法律责任由使用者自行承担，开发者不承担任何连带责任。

## 主要特性

### 🔧 可视化工作流引擎

- 基于节点的可视化工作流编辑器，拖拽即可搭建自定义流程
- 支持多种节点类型：HTTP 请求、Python 脚本、文本处理、条件分支、循环控制等
- 内置 NFO 元数据生成节点（电影 / 剧集 / 单集），兼容 Kodi NFO 格式
- 支持从 GitHub 仓库导入社区工作流模板，一键复用
- 支持定时执行（Cron / 固定间隔 / 指定时间），自动化运行工作流
- 内置 Jinja2 模板引擎与 JSONPath 提取器，灵活处理各类数据

### 🔍 索引器（资源搜索）

- 索引器完全由工作流驱动，可对接任意资源站点
- 支持搜索、详情、认证等完整交互流程
- 支持看板模式展示搜索结果
- 支持收藏资源、记录搜索历史

### 📺 媒体库管理

- 支持电影和电视剧两种媒体库类型
- 文件系统实时监控，新增 / 删除 / 移动文件自动更新媒体库
- 层级化的媒体项目结构（剧集 → 季 → 单集）
- NFO 元数据自动解析，支持从文件名和 NFO 文件提取信息
- 支持多语言元数据（英文 / 中文）

### 📥 下载管理

- 支持多种下载器：**Aria2**、**Transmission**、**qBittorrent**
- 下载器配置通过 YAML 定义，支持自定义适配更多下载器
- 下载计划：设置关键词与过滤规则，按计划自动从索引器获取资源并下载
- 下载完成后自动转移文件到媒体库，支持硬链接、软链接、移动、复制四种方式
- 支持磁力链接和种子文件

### 💬 弹幕支持

- 工作流可抓取弹幕数据并与视频关联
- 弹幕数据包含 ID、文本内容、时间戳

### 🎬 在线播放

- 内置视频播放器（基于 xgplayer），支持 FLV、HLS、MP4 格式
- 支持 HTTP Range 请求，可快速拖动和在线流式播放
- 播放进度记录，支持续播

### 📱 PWA 支持

- 支持作为 PWA 应用安装到桌面或移动设备
- 自动生成多尺寸图标（64×64、192×192、512×512）

### 👥 用户与权限

- 多用户支持，区分管理员和普通用户角色
- 细粒度权限控制：可按媒体库、索引器分配访问权限
- 在线用户会话管理
- 个人偏好设置与头像自定义

### 🌐 国际化

- 支持中文（简体）和英文界面

## 快速开始

### 环境要求

- Python >= 3.13
- Node.js（推荐 LTS 版本）
- pnpm

### 安装与运行

```bash
# 1. 克隆项目
git clone https://github.com/xylitol/kaloscope.git
cd kaloscope

# 2. 安装 Git hooks（可选，开发用）
pre-commit install

# 3. 构建前端
cd frontend
pnpm install
pnpm run build
cd ..

# 4. 安装后端依赖并启动
cd backend
poetry install
poetry run sanic app.main:app
```

启动后访问 `http://localhost:8000`，首次使用需创建管理员账户。

## 项目结构

```
kaloscope/
├── frontend/                # 前端（SvelteKit）
│   ├── src/
│   │   ├── routes/          # 页面路由
│   │   │   ├── login/       # 登录页
│   │   │   ├── setup/       # 初始设置页
│   │   │   └── (app)/       # 主功能页面
│   │   │       ├── dashboard/     # 仪表盘
│   │   │       ├── websearch/     # 资源搜索
│   │   │       ├── downloads/     # 下载管理
│   │   │       ├── medialibs/     # 媒体库浏览
│   │   │       └── settings/      # 系统设置
│   │   └── lib/             # 组件与工具库
│   └── static/              # 静态资源
├── backend/                 # 后端（Sanic）
│   └── app/
│       ├── main.py          # 应用入口
│       ├── routes/          # API 路由
│       ├── models/          # 数据模型
│       ├── services/        # 业务逻辑
│       └── core/            # 核心模块
│           ├── dl/          # 下载器适配
│           ├── flow/        # 工作流引擎
│           └── media/       # 媒体库管理
└── workspace/               # 运行时数据
    ├── database/            # 数据库文件
    ├── downloads/           # 下载文件
    ├── images/              # 图片缓存
    └── repositories/        # 工作流仓库
```

## 特别鸣谢

本项目基于众多优秀的开源项目构建而成，在此向所有这些项目的开发者和贡献者表示衷心的感谢。
完整的第三方依赖列表及其开源协议请查看 [LICENSES.md](LICENSES.md) 文件。

## 开源协议

本项目基于 [GPLv3](LICENSE) 开源协议发布。
