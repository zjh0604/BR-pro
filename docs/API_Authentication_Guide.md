# 商单推荐系统接口鉴权对接指南（后端专用）

## 📋 概述
本指南面向**后端对后端**调用场景，详细说明如何与商单推荐系统进行安全接口对接，确保接口安全、数据防篡改、防重放。

---

## 1. 鉴权机制简介
- **AES 对称加密**：保护参数内容不被窃取
- **HMAC-SHA256 签名**：防止参数被篡改
- **Nonce 防重放**：防止请求被重复利用
- **时间戳校验**：防止延迟/重放攻击

---

## 2. 密钥配置

### 2.1 AES 密钥
- **长度**：16字节（128位）
- **配置方式**：双方后端在各自配置文件或环境变量中**约定一致**
- **示例（目前设置）**：
  ```env
  AES_KEY=1234567890123456
  ```

### 2.2 HMAC 密钥
- **配置方式**：同上，双方约定一致
- **示例**：
  ```env
  HMAC_KEY=your_hmac_secret_key
  ```

> ⚠️ **注意：密钥绝不能硬编码在代码仓库，建议通过环境变量或安全配置管理。**

---

## 3. 安全参数构造

每次请求需构造如下参数：

| 字段      | 说明                 | 必须 | 示例值                  |
|-----------|----------------------|------|------------------------|
| token     | 调用方用户Token      | 否   | "test_token_123"       |
| userId    | 调用方用户ID         | 否   | "user_456"             |
| timestamp | 当前毫秒时间戳       | 是   | 1712345678000           |
| url       | 当前请求路径         | 是   | "/api/orders/submit"    |
| platform  | 固定为"backend"      | 是   | "backend"              |
| nonce     | 随机字符串，防重放   | 是   | "a1b2c3d4e5f6g7h8i9j0" |
| sign      | 签名，见下文         | 是   | "Base64签名"           |

- **nonce**：由调用方后端生成，保证唯一性（如uuid、时间戳+随机数等）
- **platform**：建议固定为`backend`，便于区分调用来源

---

## 4. 签名生成流程

1. **参数排序**：将所有参数（除 sign 外）按字母顺序排序
2. **拼接字符串**：`key1=value1&key2=value2&...`
3. **HMAC-SHA256签名**：用 HMAC_KEY 生成签名
4. **Base64编码**：将签名结果Base64编码，填入 sign 字段

**Python示例：**
```python
import hmac, hashlib, base64

def get_signature_data(payload):
    data = {k: v for k, v in payload.items() if k != 'sign'}
    return '&'.join(f'{k}={v}' for k, v in sorted(data.items()))

def sign(data, key):
    return base64.b64encode(hmac.new(key.encode(), data.encode(), hashlib.sha256).digest()).decode()
```

---

## 5. AES 加密流程

- 用 AES_KEY 对参数 JSON 字符串加密（AES-128-ECB，PKCS7Padding），Base64 编码

**Python示例：**
```python
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
import base64, json

def aes_encrypt(plain, key):
    cipher = AES.new(key, AES.MODE_ECB)
    return base64.b64encode(cipher.encrypt(pad(plain.encode(), AES.block_size))).decode()

payload = {...}  # 构造好的参数字典
json_str = json.dumps(payload, ensure_ascii=False)
encrypted = aes_encrypt(json_str, AES_KEY.encode())
```

---

## 6. 请求头设置
- `x-encrypt-key`：放加密后的参数字符串
- `Authorization`：如有token，放`Bearer <token>`
- `Content-Type`：`application/json`

---

## 7. 完整请求流程（伪代码）

```java
import javax.crypto.Cipher;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.util.*;
import java.nio.charset.StandardCharsets;
import java.util.Base64;
import java.net.HttpURLConnection;
import java.net.URL;
import java.io.OutputStream;

public class SecureApiClient {

    // 配置密钥（与服务端保持一致，建议从配置文件/环境变量读取）
    private static final String AES_KEY = "1234567890123456"; // 16字节
    private static final String HMAC_KEY = "your_hmac_secret_key";

    // 生成随机nonce
    public static String generateNonce() {
        return UUID.randomUUID().toString().replace("-", "");
    }

    // 构造签名数据字符串
    public static String getSignatureData(Map<String, Object> payload) {
        Map<String, Object> sorted = new TreeMap<>(payload);
        sorted.remove("sign");
        StringBuilder sb = new StringBuilder();
        for (Map.Entry<String, Object> entry : sorted.entrySet()) {
            if (sb.length() > 0) sb.append("&");
            sb.append(entry.getKey()).append("=").append(entry.getValue());
        }
        return sb.toString();
    }

    // 生成HMAC-SHA256签名并Base64编码
    public static String sign(String data, String key) throws Exception {
        Mac mac = Mac.getInstance("HmacSHA256");
        SecretKeySpec secretKey = new SecretKeySpec(key.getBytes(StandardCharsets.UTF_8), "HmacSHA256");
        mac.init(secretKey);
        byte[] hash = mac.doFinal(data.getBytes(StandardCharsets.UTF_8));
        return Base64.getEncoder().encodeToString(hash);
    }

    // AES-128-ECB加密并Base64编码
    public static String aesEncrypt(String plainText, String key) throws Exception {
        Cipher cipher = Cipher.getInstance("AES/ECB/PKCS5Padding");
        SecretKeySpec keySpec = new SecretKeySpec(key.getBytes(StandardCharsets.UTF_8), "AES");
        cipher.init(Cipher.ENCRYPT_MODE, keySpec);
        byte[] encrypted = cipher.doFinal(plainText.getBytes(StandardCharsets.UTF_8));
        return Base64.getEncoder().encodeToString(encrypted);
    }

    // 构造安全头部
    public static Map<String, String> buildHeaders(String url, String token, String userId) throws Exception {
        Map<String, Object> payload = new HashMap<>();
        payload.put("token", token);
        payload.put("userId", userId);
        payload.put("timestamp", System.currentTimeMillis());
        payload.put("url", url);
        payload.put("platform", "backend");
        payload.put("nonce", generateNonce());
        payload.put("sign", "placeholder");

        String sigData = getSignatureData(payload);
        payload.put("sign", sign(sigData, HMAC_KEY));

        String json = new com.fasterxml.jackson.databind.ObjectMapper().writeValueAsString(payload);
        String encrypted = aesEncrypt(json, AES_KEY);

        Map<String, String> headers = new HashMap<>();
        headers.put("x-encrypt-key", encrypted);
        if (token != null && !token.isEmpty()) {
            headers.put("Authorization", "Bearer " + token);
        }
        headers.put("Content-Type", "application/json");
        return headers;
    }

    // 发送POST请求
    public static String post(String apiUrl, String urlPath, String token, String userId, String bodyJson) throws Exception {
        Map<String, String> headers = buildHeaders(urlPath, token, userId);

        URL url = new URL(apiUrl + urlPath);
        HttpURLConnection conn = (HttpURLConnection) url.openConnection();
        conn.setRequestMethod("POST");
        for (Map.Entry<String, String> entry : headers.entrySet()) {
            conn.setRequestProperty(entry.getKey(), entry.getValue());
        }
        conn.setDoOutput(true);

        try (OutputStream os = conn.getOutputStream()) {
            os.write(bodyJson.getBytes(StandardCharsets.UTF_8));
        }

        int responseCode = conn.getResponseCode();
        String response = new java.util.Scanner(conn.getInputStream(), "UTF-8").useDelimiter("\\A").next();
        System.out.println("响应码: " + responseCode);
        System.out.println("响应内容: " + response);
        return response;
    }

    // 示例调用
    public static void main(String[] args) throws Exception {
        String apiUrl = "http://localhost:8000";
        String urlPath = "/api/orders/submit";
        String token = "test_token_123";
        String userId = "user_456";
        String bodyJson = "{ \"user_id\": \"user_456\", \"corresponding_role\": \"buyer\", \"classification\": \"电子产品\", \"wish_title\": \"测试订单\", \"wish_details\": \"这是一个测试订单\", \"is_platform_order\": false, \"priority\": 5 }";

        post(apiUrl, urlPath, token, userId, bodyJson);
    }
}
```

---

## 8. 服务端校验流程
- 解密 x-encrypt-key，解析参数
- 校验时间戳（±1分钟）、url、nonce（防重放）、签名、token（如有）
- 校验通过才放行，否则返回 401

---

## 9. 常见问题

- **密钥如何确保一致？**
  - 由项目负责人统一下发，双方配置文件/环境变量保持一致。
  - 推荐用 `.env` 或安全配置中心管理。
- **nonce 如何生成？**
  - 用 uuid、时间戳+随机数等，保证唯一性。
- **平台字段如何设置？**
  - 固定为 `backend`。
- **签名、加密算法如何保证一致？**
  - 严格按本指南示例实现，参数顺序、算法、密钥完全一致。
- **密钥变更如何处理？**
  - 需双方同步变更，建议定期轮换。

---

## 10. 对接Checklist
- [ ] AES_KEY、HMAC_KEY 配置一致
- [ ] 参数构造、签名、加密流程一致
- [ ] 请求头设置正确
- [ ] 非白名单接口均需鉴权
- [ ] 测试通过所有典型场景

