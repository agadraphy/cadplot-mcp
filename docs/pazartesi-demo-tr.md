# Pazartesi CadPlot MCP demo akışı

## Sunum öncesi tek komut kontrol

```powershell
.\scripts\run-demo-rehearsal.ps1 `
  -AutoCADApiDir "C:\Program Files\Autodesk\AutoCAD 2025" `
  -AuditDependencies `
  -WriteReport
```

Bu komut tüm yerel preflight zincirini çalıştırır, ardından temiz Git commit'ini ve üretilen wheel'in
SHA-256 hash'ini rapora bağlar. AutoCAD'i açmaz ve canlı plot kanıtı üretmez. Son JSON'da
`passed=true`, `local_demo_ready=true`, `worktree_clean=true` görülmeden demoya başlamayın;
`live_publish_proven=false` değeri canlı pilot yapılana kadar doğru kalmalıdır.
`dependency_audit.passed=true`, Python/.NET açık sayılarının `0` ve
`python_license_inventory.unknown_count=0` olduğunu da kontrol edin. Bu tarama ağdaki güncel
veritabanlarının o andaki sonucudur; AutoCAD veya canlı yayın kanıtı değildir.
`durable_queue_recovery.passed=true` ve on beş exact senaryonun geçtiğini de gösterin: başlamamış onay
aynı kimlikle geri yüklenir, receipt'siz yarıda kesilen iş otomatik replay edilmez, terminal receipt
durumu geri gelir, bozulmuş/unsigned/başka anahtarla imzalanmış intent reddedilir, DPAPI anahtarı
workspace dışında kalır ve tamamlanmış iş yeniden kuyruğa alınmaz. `authentication_scheme` değeri
`windows-dpapi-current-user+hmac-sha256-v1` olmalıdır.
Ek olarak exact pending iptali restart sonrasında `Cancelled` kalır, değiştirilmiş iptal marker'ı
reddedilir, aynı iptal güvenle tekrar çağrılabilir ve `Running` iş zorla kesilemez.
`net45_dpapi_runtime_proven=true` ve `net45_core_image_runtime=v4.0.30319`, aynı anahtar yolunun
AutoCAD 2016 hedefindeki gerçek .NET Framework core DLL'i içinde de çalıştığını gösterir; bu yine
AutoCAD'in açıldığı veya plot yapıldığı anlamına gelmez.
`-WriteReport`, aynı son JSON'u yeni ve üzerine yazılmayan bir geçici dosyada saklar; ekrandaki
`report_path` değerini demo kaydı olarak koruyun.

Tam Autodesk 2016 `R20.1` ve 2025 `R25.0` SDK klasörleri yetkili makinede hazırsa,
`scripts/build-complete-release.ps1` aynı provayı, iki adapter derlemesini, bundle/release-kit
üretimini ve iki bağımsız doğrulamayı tek komutta yapar. SDK indirme/lisans kabulü otomatik değildir;
`docs/autodesk-sdk-prerequisites.md` belgesindeki resmi Autodesk kapısı operatör tarafından
tamamlanmalıdır.

Preflight içinde 300 sentetik kaynak da 20'lik sayfalama/staging batch'leriyle uçtan uca prova edilir.
Readiness raporunda `synthetic_batch_rehearsal.target_drawings=300`, `staged=300`,
`outputs_complete=300`, `orientation_mismatch_rejected=true`, `execution_verified=0`,
`publish_verified=0` ve
`manual_review_without_receipts=300` görülmelidir. Son iki sıfır hata değildir: AutoCAD receipt'i
olmayan sentetik PDF'lerin canlı başarı gibi sunulmasını engeller.

Taşınabilir yerel demo kitini yalnız bu rapordan üretin:

```powershell
.\scripts\build-demo-kit.ps1 -ReadinessReport "<report_path>"
```

Komut, üzerine yazmadan `cadplot-demo-delivery-<commit>` klasörü üretir. Klasör; tam commit'e
bağlı kit dizinini, aynı dizinin ZIP'ini ve ikisini birbirine bağlayan `demo-kit-build.json` dış
manifestini içerir. Kitte kaynak ZIP'i, izole kurulumu denenmiş wheel ve hash manifesti bulunur.
Şirket varlığı veya Autodesk DLL'i içermez; canlı AutoCAD eklenti bundle'ı değildir.

Klasörü göstermeden veya başka bilgisayara taşımadan önce teslim kökünde arşiv doğrulayıcıyı
çalıştırın:

```powershell
.\cadplot-demo-kit-<commit>\verify-demo-archive.ps1 -DeliveryRoot .
```

Sonuçta `Passed=true`, `MachinePathsIncluded=false`, `AutoCADLaunched=false` ve
`LivePublishProven=false` görülmelidir. Kontrol, ZIP'i çıkarmadan exact giriş kümesini ve her girişin
hashini kit klasörüyle; iç manifest/SBOM kimliğini de dış manifestle karşılaştırır. ZIP açıldıktan
sonra aynı kitteki `verify-demo-kit.ps1 -KitRoot .` iç doğrulaması bağımsız tekrar çalıştırılabilir.
Paketin kimden geldiğini kanıtlamak için commit/hash değerini ayrıca güvenilir kanaldan karşılaştırın.

`licensed_live_pilot_ready=false` hata değildir: lisanslı hedef makine, yetkili şirket plot kaynakları
ve tek-pafta görsel kanıtı olmadan canlı başarı iddiası üretilmesini özellikle engeller.

Bu demo yalnız lisanslı şirket iş istasyonunda, sorumlunun izin verdiği anonim veya üretim dışı bir
DWG kopyasıyla yapılır. Şirket DWG/PC3/PMP/CTB/STB/DWT dosyaları kişisel bilgisayara veya GitHub'a
taşınmaz.

## Önceden hazırla

- AutoCAD 2016 ve 2025 için sürüme uygun managed SDK/reference klasörleri;
- bir paftalık yetkili DWG kopyası ve elle kabul edilmiş referans PDF;
- gerçek page setup, plotter, plot style ve canonical media adlarının yazıldığı yerel `config.yaml`;
- kaynaklardan tamamen ayrı yerel `workspace_root`;
- temiz commit'ten yeni klasöre üretilmiş `scripts/build-bundle.ps1` çıktısı,
  `bundle-build.json` ve geçen `scripts/verify-bundle-release.ps1` raporu.

## 10 dakikalık gösterim

1. AutoCAD kapalıyken build JSON'daki tam bundle yolunu
   `scripts/install-bundle.ps1 -SourceBundle "<tam yol>" -WhatIf` ile gösterin. Kaynak, tam release
   kökündeki `CadPlotMcp.bundle` olmalı; kurucu kardeş ZIP'i, `bundle-build.json` manifestini ve
   matching-SDK kanıtını yeniden doğrulamalıdır. Protokol-only test anahtarını kullanmayın.
2. İlk açılışta publish kapalı kalsın. `get_autocad_plugin_status` ile doğru adapter, `ACADVER`,
   `workspaceConfigured=true` ve `publishEnabled=false` değerlerini gösterin. `buildCommit` release
   commit'iyle, `pluginSha256` ise `bundle-build.json` içindeki ilgili adapter DLL hash'iyle aynı
   olmalıdır.
3. `inspect_drawing` çalıştırıp bulunan çerçeve, mevcut layout, page setup, plotter, media ve stili
   ekranda karşılaştırın.
4. `create_publish_plan` çalıştırın. Ölçek, yön, plot window, hedef layout ve blocker listesini
   sorumluya okutun.
5. Plan ID açıkça kabul edildikten sonra `stage_publish_job` çalıştırın. Orijinal yerine hash'i
   doğrulanmış izole kopya oluştuğunu gösterin.
6. `validate_staged_job` ile aynı manifesti AutoCAD eklentisine bağımsız doğrulatın.
7. Sorumlu gerçek yazma pilotuna izin verirse AutoCAD'i kapatın,
   `CADPLOT_ENABLE_PUBLISH=1` ayarlayıp yeniden açın. Önceden açık AutoCAD oturumunda çevre
   değişkeni etkili sayılmaz.
8. Yalnız bir paftayı, staging sonucundaki tam `plan_id + manifest_sha256` ile sıraya alın.
9. Canlı durum `Succeeded` olduktan sonra `read_publish_receipt` ve
   `audit_publish_outputs` çalıştırın. Schema-v2 receipt için `output_count`, `outputs_sha256` ve
   `receipt_output_binding_verified=true` gösterilmelidir. Kabul sonucu yalnız
   `publish_verified=true` ise geçer.
10. PDF'yi referansla yan yana açıp yön, crop, gerçek ölçek, lineweight, CTB/STB, font ve title
    block kontrolünü sorumluya yaptırın.

AutoCAD beklenmedik biçimde kapanırsa yeniden açıldığında `queueRecoveredOnStartup`,
`queueInterruptedOnStartup`, `queueCancelledOnStartup` ve `queueAuthentication` alanlarını okuyun. Yalnız aynı
Windows kullanıcısının DPAPI anahtarıyla doğrulanan, hiç başlamamış `Pending` intent devam eder;
`job_interrupted` görülen işi aynı staged job üzerinde tekrar kuyruklamayın.
Yanlış profil fark edilirse exact `plan_id` ve `manifest_sha256` ile yalnız `Pending` işi
`cancel_publish_job` üzerinden iptal edin. Sonuç `Cancelled` olmalı; `Running` işi zorla kesmeyin.

## Söylenecek net sınırlar

- 2024 API compile probe, 2016 veya 2025 canlı uyumluluk kanıtı değildir.
- 2016 sonucu 2025'i; 2025 sonucu 2016'yı kanıtlamaz. İki pilot ayrı kaydedilir.
- Geçerli PDF dosyasının tek başına varlığı AutoCAD yürütme kanıtı değildir; başarılı receipt de
  aynı PDF byte'larına canonical çıktı digest'iyle bağlı olmalı ve
  `receipt_output_binding_verified=true` dönmelidir.
- Profilde `template_layout` yoksa executor boş layout + tam sayfa viewport kurar. Kaynak DWG
  içinde tek floating viewport'lu onaylı template varsa onu klonlayabilir. Harici DWG/DWT için
  `template_roots` + exact yol/hash/layout sözleşmesi gerekir; yalnız job-local kopya import edilir.
  Bu yol iki lisanslı sürümde ayrıca kabul edilmeden canlı destek kanıtlanmış sayılmaz.
- Normal ChatGPT web oturumu yerel AutoCAD'e kendiliğinden erişmez. İlk demo yerel MCP istemcisiyle
  yapılır; merkezi şirket ChatGPT bağlantısı IT onaylı güvenli connector/tunnel işidir.

## Pilot başarısız olursa

Hata kodunu, manifest/receipt hash'lerini ve çıktı audit'ini kaydedin. Var olan PDF'nin veya job'ın
üstüne yazmayın. Nedeni düzeltip yeni plan onayıyla yeni bir staging job oluşturun. Canlı kabul
geçmeden “production ready” demeyin; çalışan dry-run ve güvenlik zincirini yine gösterebilirsiniz.

## Demo sonrası güvenli geri alma

Release kit ile kurulduysa AutoCAD'i kapatın; `uninstall-release-kit.ps1` komutunu önce `-WhatIf`,
sonra aynı release kökü ve kurulum makbuzuyla gerçek çalıştırın. Bundle önce, Python ortamı sonra
kaldırılır. Pilot klasörü, şirket config'i, yetkili girdiler ve kanıt makbuzu bilerek korunur; betik
AutoCAD sürecini kendisi kapatmaz.
