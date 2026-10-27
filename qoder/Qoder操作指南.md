# Qoder 赛题操作指南（你来做）

题面硬性要求：**项目主要开发工作必须在 Qoder 中完成**，并通过 **Qoder Sites** 发布项目网站。
所以下面这些步骤必须由你在 Qoder 里操作（我无法代做开发过程）。

## 第 1 步：注册/登录 Qoder

1. 打开 Qoder IDE（已安装），或下载桌面端/CLI：https://qoder.com/download
2. 注册账号并登录
3. 学生可领权益（题面：Qoder CN 个人专业版 Pro 1 个月）：
   - 进入 Qoder x OceanBase 大赛专属权益页
   - 支付宝完成学生认证 → 领 300 元代金券 → 下单「个人专业版 Pro 1 个月」
4. 记录你的 **Qoder 账号 UID**（设置/个人中心里可见，提交材料要用）

## 第 2 步：用 Qoder 开发项目

1. Qoder 里 **新建项目**（目录建议 `D:\OceanBase\qoder-workspace\paperlens`）
2. 打开 Qoder 的 Agent 对话（Agent 模式）
3. 把 `项目方案.md` 的**全文**粘贴进去，发送
4. Qoder 会分步生成代码。每完成一步：
   - **截图保存**（后面作为"Qoder 开发记录"提交）
   - 让它继续："继续完成下一步" / "按验收标准自测"
5. 遇到报错，把报错发给 Qoder 修复（同样截图）

⚠️ 截图要点：至少包含 **需求拆解、代码生成、调试修复** 三类对话，能看出主要功能由 Qoder 完成。

## 第 3 步：本地跑通（我可以帮你验收）

```powershell
docker start seekdb
cd D:\OceanBase\qoder-workspace\paperlens
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\uvicorn app.main:app --port 8000
```

打开 http://127.0.0.1:8000 ，按方案里的验收标准逐条测试。
跑不通就把报错发我，或让 Qoder 修。

## 第 4 步：用 Qoder Sites 发布项目网站

1. 在 Qoder 里使用 **Sites** 功能创建项目网站
2. 网站内容要求：项目简介、解决的问题、核心功能、演示截图/视频、代码仓库链接、体验入口（如有）
3. 可见范围设为「**任何人都可访问**」（企业版账号不行，用个人账号）
4. 发布后复制网址，用**无痕窗口**验证未登录可访问

## 第 5 步：提交材料清单

| 材料 | 来源 |
|---|---|
| 项目网站网址 | Qoder Sites 发布后复制 |
| 代码仓库 | 把项目代码提交到你的 GitHub 仓库（可新建 `qoder-paperlens` 私有/公开仓库），提供地址 |
| 运行说明 | Qoder 生成的 README（核对完整） |
| 验证样例 | 按方案验收标准录一组截图/录屏 |
| Qoder 开发记录 | 第 2 步的对话截图 + **Qoder 账号 UID** |

提交入口：大赛测试平台 → Qoder 赛题 → 填网站网址、仓库地址、Qoder UID，上传其余材料。

## 我可以帮你做的部分

- 方案与提示词（已完成）
- seekdb 连接与建表 SQL 已写进方案
- 你的项目跑起来后的**验收测试**（发我报错或截图）
- 代码仓库的整理与提交（你给我代码路径即可）
- 项目网站文案润色
