# POLYBOT

合规优先的多账户流动性奖励挂单框架（Python CLI）。

本项目提供一个命令行工具：
- 从配置加载多账户。
- 按奖励参数过滤市场（如 `min_incentive_size`）。
- 终端实时面板显示状态。
- 默认 `dry_run` 模式，避免误下单。
- 使用私钥签名下单。

> 仅用于合规场景，禁止绕过平台规则或地理限制。

## 本地运行（Windows）

```bash
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt

# 复制配置模板并填写
copy config.example.yaml config.yaml

# 设置私钥环境变量（每个账户一条）
set POLYBOT_ACCT1_PRIVATE_KEY=0x...
set POLYBOT_ACCT2_PRIVATE_KEY=0x...

# 启动
python -m polybot.cli run --config config.yaml
```

## 使用 .env（推荐，免重复配置）

在项目根目录创建 `.env` 文件：

```bash
POLYBOT_ACCT1_PRIVATE_KEY=0x...
POLYBOT_ACCT2_PRIVATE_KEY=0x...
```

启动时会自动读取 `.env`。

## 配置

请参考 `config.example.yaml`。密钥放在环境变量里，不要写进配置文件。

## 备注

- `min_incentive_size` 高于 `max_order_usdc` 的市场会被跳过。
- 只有将 `dry_run: false` 才会真实下单。
- 通过 `order_refresh_seconds` 控制换价频率，避免重复下单。
- 价格偏离超过 `cancel_replace_threshold_bps` 时会撤单并重挂。
