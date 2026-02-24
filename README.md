# 🧑‍🍳 Emoji Kitchen - AstrBot Plugin

发送两个 emoji，自动合成 Google Emoji Kitchen 风格的混搭图片！

## 功能

- **`/mix 😀😺`** — 命令模式，合成两个 emoji
- **直接发送两个 emoji** — 如 `😀😺`，自动触发合成
- 支持 **10 万+** emoji 组合（数据来自 [emoji-kitchen-backend](https://github.com/xsalazar/emoji-kitchen-backend)）
- 多镜像源下载 + 重试 + 本地缓存

## 安装

在 AstrBot 中通过仓库地址安装：

```
https://github.com/camera-2018/astrbot_plugin_emojikitchen
```

## 依赖

- `regex` — Unicode emoji 正则匹配（通过 `requirements.txt` 自动安装）

## 示例

| 输入 | 效果 |
|------|------|
| `/mix 😀😺` | 合成 😀 + 😺 |
| `🔥💀` | 自动合成 🔥 + 💀 |
| `/mix 🐱🐶` | 合成 🐱 + 🐶 |

## 开发

```bash
# 运行测试
python -m pytest test_main.py -v
```

## License

GPL-3.0
