# 黑客松大模型 使用文档

> Source: https://anker-in.feishu.cn/docx/HAtJdgo45otz2Px31bYcu3PDnRf  
> Retrieved: 2026-10-08  
> Document-body images: 0; hyperlinks: 0. UI avatars are not document attachments.

## 一、模型开放范围

为支持本次赛事顺利开展，主办方将向参赛选手开放以下模型能力：

| 模型名称 | 输入支持类型 | 输出支持类型 | 上下文长度 | 最大输出 | 模型 ID（OpenAI 协议） | 模型 ID（Anthropic 协议） |
| --- | --- | --- | --- | --- | --- | --- |
| GLM-5.2 | 文本 | 文本 | 1M tokens | 128K tokens | hackathon/v_model/glm-5.2 | hackathon/v_model/anthropic-glm-5.2 |
| DeepSeek-V4-Pro | 文本 | 文本 | 1M tokens | 384K tokens | hackathon/v_model/deepseek-v4-pro | hackathon/v_model/anthropic-deepseek-v4-pro |
| Qwen3.8-Max | 文本、图片、视频 | 文本 | 1M tokens | 128K tokens | hackathon/v_model/qwen-max | 暂未开放 |
| AWS Bedrock Nova Pro | 文本、图片、视频 | 文本 | 300K tokens | 10K tokens | hackathon/v_model/nova-pro | hackathon/v_model/nova-pro |
| qwen-audio-3.0-asr-flash | 音频 | 文本 |  |  | hackathon/v_model/qwen-audio-3.0-asr-flash | 暂未提供 |
| qwen-audio-3.0-tts-flash | 文本 | 音频 |  |  | hackathon/v_model/qwen-audio-3.0-tts-flash | 暂未提供 |
| Kimi-K3 | 文本、图片、视频 | 文本 | 1M tokens | 最大 1M tokens（默认 128K tokens） | hackathon/v_model/kimi-k3 | hackathon/v_model/anthropic-kimi-k3 |
| DeepSeek-V4.1-Flash | 文本、图片 | 文本 | 1M tokens | 384K tokens | hackathon/v_model/deepseek-v4.1-flash | hackathon/v_model/anthropic-deepseek-v4-1-flash |
| doubao-seedream-5-0-pro | 图片，文本 | 图片 |  |  | hackathon/v_model/doubao-seedream-5-0-pro |  |

---

## 二、使用方式

### Opencode

a. 自行安装好opencode

b. 配置主办方模型和地址，和AI key

配置当前用户的公司ai key和模型，将用户目录下面的：~/.config/opencode/opencode.jsonc 替换成下面内容，没有就新建一个：

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "plugin": [
  ],
  "model": "v_router-cn/hackathon/v_model/anthropic-glm-5.2",
  "provider": {
    "anker-router": {
      "npm": "@ai-sdk/openai-compatible",
      "options": {
        "baseURL": "https://ai-router-cn-pub.anker-in.com",
        "apiKey": "这里填写分配的API_KEY（与Claude Code的KEY是同一个）"
      },
      "models": {
        "hackathon/v_model/anthropic-glm-5.2": {
          "id": "hackathon/v_model/anthropic-glm-5.2",
          "name": "anthropic-glm-5.2"
        },
        "hackathon/v_model/glm-5.2": {
          "id": "hackathon/v_model/glm-5.2",
          "name": "glm-5.2"
        },
        "hackathon/v_model/anthropic-deepseek-v4-pro": {
          "id": "hackathon/v_model/anthropic-deepseek-v4-pro",
          "name": "anthropic-deepseek-v4-pro"
        },
        "hackathon/v_model/deepseek-v4-pro": {
          "id": "hackathon/v_model/deepseek-v4-pro",
          "name": "deepseek-v4-pro"
        },
        "hackathon/v_model/qwen-max": {
          "id": "hackathon/v_model/qwen-max",
          "name": "qwen3.8-max"
        }
      }
    }
  }
}
```
