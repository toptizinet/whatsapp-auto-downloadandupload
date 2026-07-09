# WhatsApp 版本更新监控（GitHub Actions）

自动定时检查 Uptodown 上 WhatsApp 的版本号，一旦有更新就通过 Server酱（微信）和/或 Telegram 推送通知，完全免费，不需要自己开电脑。

## 工作原理

1. GitHub Actions 按 cron 定时（默认每小时）跑一次 `check_whatsapp_version.py`
2. 脚本先访问搜索页找到 WhatsApp 详情页链接，再从详情页抓版本号（详情页结构比搜索结果页更稳定，不容易因为广告/排序变化而误判）
3. 把当前版本号和仓库里 `last_version.json` 记录的版本号做对比
4. 不一样就发通知，然后把新版本号写回 `last_version.json` 并提交到仓库（这样下次 Actions 运行时还能读到"上次的版本"）

## 部署步骤

### 1. 建仓库

把这几个文件放进一个新的 GitHub 仓库（public 或 private 都行，private 的话免费额度是每月 2000 分钟，这种任务完全够用）。

### 2. 配置推送方式（至少选一个）

**Server酱（推荐，微信推送，国内直连稳定）**

1. 打开 https://sct.ftqq.com/ ，用微信扫码登录
2. 拿到你的 SendKey（形如 `SCTxxxxxxxxxx`）
3. 在仓库 Settings → Secrets and variables → Actions → New repository secret，添加：
   - Name: `SERVERCHAN_KEY`
   - Value: 你的 SendKey

**Telegram（如果你能正常访问 Telegram，几乎实时）**

1. 找 @BotFather 创建一个 Bot，拿到 Bot Token
2. 找 @userinfobot 或类似的 Bot 拿到你的 chat_id
3. 添加两个 Secret：
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`

两个都配置的话会同时收到两边的通知，只配一个也完全没问题（脚本里没配的那个会自动跳过）。

### 3. 打开 Actions

仓库的 Actions 标签页里，如果提示需要手动启用 workflow，点一下启用。

### 4. 手动跑一次测试

Actions → Check WhatsApp Version → Run workflow，手动触发一次，看日志：

- 第一次运行会提示"首次运行，已记录版本号，不发送通知"，这是正常的（脚本会把当前版本存进 `last_version.json` 作为基准，避免第一次运行就误报"更新"）
- 之后可以手动把 `last_version.json` 里的版本号改成一个旧版本号，再手动跑一次，应该就能收到通知了，用来验证推送链路通不通

### 5. 调整检查频率

默认是每小时整点（`cron: '0 * * * *'`），改 `.github/workflows/check-version.yml` 里的 cron 表达式即可。注意 GitHub Actions 的定时任务在负载高峰期可能有几分钟延迟，不是绝对精确的。

## 如果脚本抓取失败

Uptodown 可能会调整页面结构，导致脚本抓不到链接或版本号。如果日志里报错"页面结构可能已发生变化"，说明网站改版了，需要打开对应页面用浏览器"检查元素"看一下版本号现在放在哪个标签里，然后改一下 `check_whatsapp_version.py` 里 `get_version_from_detail` 函数的选择器。

## 文件说明

- `check_whatsapp_version.py`：主脚本
- `requirements.txt`：Python 依赖
- `last_version.json`：记录上次检测到的版本号（脚本自动读写，初始为空）
- `.github/workflows/check-version.yml`：定时任务配置
