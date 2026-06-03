# 认证与设置

## 功能说明

操作员用邮箱密码登录 Console，在设置页连接个人 Gmail、查看环境（TEST/LIVE）、管理用户（owner）与审计记录。首次启动后端会创建 `owner@console.app` 一次性密码（见日志）。

## 操作员路径

| 路径 | 页面 |
|------|------|
| `/login` | `LoginPage.tsx` |
| `/settings` | `SettingsPage.tsx` |

## 关键文件

| 层 | 文件 |
|----|------|
| FE | `pages/LoginPage.tsx`, `pages/SettingsPage.tsx` |
| BE | `routers/auth.py`, `routers/admin.py`（审计、TEST 清理） |
| 核心 | `security.py`, `deps.py`（JWT、`require_role`）, `db.py`（`users`）, `audit.py` |

## 主要 API

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/auth/login` | 返回 JWT |
| GET | `/auth/me` | 当前用户 |
| POST | `/auth/users` | 创建用户（owner） |
| GET | `/admin/audit` | 审计日志 |

## 关联模块

- [gmail](../gmail/GUIDE.md) — 设置在 `SettingsPage` 发起 OAuth
- [gate-metrics](../gate-metrics/GUIDE.md) — `admin` 路由共用

## RBAC

`owner` > `operator` > `viewer`（见 `backend/README.md`）。写操作应写 `audit_log`。
