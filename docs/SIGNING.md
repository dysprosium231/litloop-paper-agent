# 给安装包签名

现在发出去的 `LitLoop_<版本>_x64-setup.exe` 与 `LitLoop_<版本>_x64_<语言>.msi` **没有签名**，用户第一次运行要过 SmartScreen「更多信息 → 仍要运行」。这一步会劝退不少人，所以签名是发布线里值得做的一项。

签名的两个作用要分开看：

- **身份**：证书让 Windows 知道「这个 exe 是谁发的」，证书被吊销时已安装的副本也会受影响；
- **声誉**：SmartScreen 对「这个发布者以前有没有人装过」单独记分。OV 证书要靠下载量慢慢攒；EV 证书一般装完就信，但贵得多。

## 三条路

| 方案 | 花钱 | 需要准备什么 | 能不能在 CI 里签 |
|---|---|---|---|
| SignPath Foundation（开源项目免费） | 0 | 公开仓库、OSI 许可证、有人维护；在 signpath.org 提交项目申请 | 能，官方提供 GitHub Action |
| Certum Open Source Code Signing | 证书免费，USB 令牌/读卡器几十欧 | 开源项目证明、身份验证、收实体令牌 | 不能，要插着令牌的机器本地签 |
| Azure Trusted Signing | 约 $10/月 | Azure 订阅 + 身份/组织验证（规则常变，以官网为准） | 能，`trusted-signing-cli` 或官方 Action |

对我们的情况（公开仓库、Apache-2.0 许可证、有 Release、CI 已经在打两个包），**先试 SignPath** 最划算：不用买硬件，签名直接在 `release.yml` 里。

## 2026-11-07：SignPath 的申请被拒了

提交申请后收到回信（Phillip，SignPath Foundation）：Foundation 计划面向「已经有了一定公开关注度」的项目，评审看的是 stars、fork、贡献者、外部文章、独立引用或讨论（Reddit、Stack Overflow、YouTube）、机构背书这类**外部信号**。这个仓库 2026-10-11 才建、当时 1 个 star，不够格，所以没发证书；信里说等社区规模起来欢迎重新申请，也提到可以先用付费订阅。

接下来的选择，按性价比排：

1. **先不签名**（现在的状态）：README 与发版说明写清「未签名、要过 SmartScreen」，等 stars/外部引用多起来再申请 Foundation。花 0，代价是安装体验差。
2. **Certum Open Source Code Signing**：证书本身免费，但必须买 USB 令牌（几十欧 + 邮寄），签名只能在插着令牌的机器上做，CI 里签不了——每次发版本机手动签完再上传。
3. **Azure Trusted Signing**：约 $10/月，能在 CI 里签；身份/组织验证的规则常变，个人开发者能否通过要先问支持。
4. **SignPath 付费订阅**：能在 CI 里签，但价格高一个量级，适合以后有机构支持再说。

不管走哪条，`release.yml` 与发版说明里的措辞都要保持一致（现在是如实的「未签名」）。

## SignPath 的申请材料

1. 仓库地址：`https://github.com/dysprosium231/litloop-paper-agent`
2. 许可证：Apache-2.0（仓库里的 `LICENSE`）
3. 说明项目做什么：学术文献 Agent，MCP 服务器 + 桌面 app，公开发布 Windows 安装包
4. 签名对象：`LitLoop_*_x64-setup.exe`、`LitLoop_*_x64_*.msi`（以及里面的 `litloop-app.exe`）
5. 通过后拿到三样东西：`organization-id`、`project-slug`、`policy-slug`，再加一个 API token

## 在 CI 里怎么接（拿到凭据之后）

`.github/workflows/release.yml` 里加一步，只在 Secrets 存在时执行：

- Secrets：`SIGNPATH_API_TOKEN`、`SIGNPATH_ORGANIZATION_ID`、`SIGNPATH_PROJECT_SLUG`、`SIGNPATH_POLICY_SLUG`
- 签名对象先放成 `unsigned/`，签完的产物再交给 `softprops/action-gh-release`；校验和要**在签名之后**算，否则用户核对的是签名前的哈希。

本地签（Certum 那条路）的命令：

```powershell
signtool sign /fd sha256 /td sha256 /tr http://timestamp.digicert.com /a LitLoop_1.18.2_x64-setup.exe
signtool verify /pa /v LitLoop_1.18.2_x64-setup.exe
Get-AuthenticodeSignature .\LitLoop_1.18.2_x64-setup.exe | Format-List Status, SignerCertificate
```

## 签名之后还要做的

- 更新 `README` 与两份发版说明：去掉「未签名」的提示，改成「已签名，发布者 <名称>」；
- SmartScreen 第一次仍可能提示，说明里要写「装的人多了就不会再拦」；
- 卸载/升级时 Windows 会按证书与 UpgradeCode 判断是不是同一个产品——换证书发布新版本时，要在干净机器上验证一次升级路径。
