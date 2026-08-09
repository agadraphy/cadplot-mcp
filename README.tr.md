# CadPlot MCP

Kurulumdan sonra kitteki salt-okunur `verify-release-install.ps1`; yerel makbuzu, release kitini,
bundle hashlerini, Python envanterini ve pilot yollarını bağımsız doğrular. Beklenen envanter config
değişikliği ayrıca raporlanır; AutoCAD açılmaz ve yayın etkinleştirilmez.
`uninstall-release-kit.ps1`, aynı makbuza bağlı `-WhatIf` destekli geri alma sağlar; doğrulanmış
bundle'ı Python'dan önce kaldırır, pilot verisini ve makbuzu bilerek korur ve yarım işlemden devam eder.
GitHub Actions bağımlılıkları immutable tam commit SHA'larına sabitlenir; CI salt-okunur izin ve
kalıcı olmayan checkout kimliği kullanır. Dependabot `uv`, NuGet ve Actions güncellemelerini izler.
Python testleri 3.11/3.12/3.13 matrisinde çalışır; ağır release/MCP/.NET preflight yalnız 3.12'de bir
kez koşar. Tam SHA kullansa bile incelenmiş allowlist dışındaki action deposu audit tarafından reddedilir.

CadPlot MCP; çok sayıdaki revize DWG dosyasını denetlenebilir biçimde incelemek, pafta
çerçevelerini şirket page setup'larıyla eşleştirmek, ölçek/layout kararlarını planlamak ve PDF
çıktılarını doğrulamak için geliştirilen açık kaynak bir MCP sunucusudur.

## Mevcut durum

Hazır olan parçalar:

- yalnızca izin verilen klasörlerde DWG tarama;
- çalışan AutoCAD üzerinden salt-okunur layout/page setup, dikdörtgen polyline ve yalnız güvenli
  koşulları sağlayan instance-attribute veya bounded nested block-definition yazısı destekli
  çerçeve inceleme; block hiçbir zaman explode edilmez;
- `70x100`, `700x1000 mm` ve `A0-A5` gibi kâğıt etiketi tanıma;
- çerçeve geometrisinden plot penceresi, yön ve izinli ölçek türetme;
- PC3 ile CTB/STB değerlerini şirket profiliyle karşılaştırma;
- hash'li ve onay kapılı dry-run planı;
- 50 DWG'ye kadar sayfalı, kaldığı yerden devam edebilen batch planlama;
- orijinale dokunmadan izole çalışma klasörüne doğrulanmış DWG kopyası alma;
- beklenen PDF'leri yol, gerçek PDF yapısı, tek sayfa, sayfa ölçüsü, boyut ve SHA-256 açısından
  denetleme;
- AutoCAD 2016 ve 2025-2026 için ayrı .NET adaptör/bundle yapısı;
- varsayılan kapalı, ana AutoCAD thread'inde çalışan sınırlı yayın kuyruğu;
- staged DWG içinde bellekte layout/page setup/viewport kurup her pafta için ayrı PDF üreten
  ortak executor.

Ortak executor, kurulu AutoCAD 2024 API DLL'lerine karşı AutoCAD açılmadan derlenmiştir. Compile
probe assembly kimliğine göre `R20.1` için `net45`, `R25.0` için `net8.0-windows`, desteklenen ara
sürümler için `net48` seçer ve bilinmeyen seriyi reddeder. Henüz tamamlanmamış kapı: aynı kodun
sürüme uygun Autodesk SDK referanslarıyla paketlenip lisanslı
AutoCAD 2016 ve 2025 üzerinde yetkili örnek DWG ve şirket plot kaynaklarıyla canlı kabul testinden
geçmesidir. Bu doğrulama yapılmadan proje üretim-hazır olarak sunulmaz.
Eklenti yüklenirken gerçek `ACADVER` değeri normalize edilir; çalışan AutoCAD sürümü ile yüklenen
adaptör uyuşmuyorsa yayın özelliği fail-closed biçimde kapalı kalır.

## Güvenlik modeli

- Keyfi AutoLISP veya AutoCAD komutu çalıştırılmaz.
- Kaynak DWG'nin üstüne yazılmaz, dosya taşınmaz veya silinmez.
- Her plan kaynak DWG'nin SHA-256 parmak izine bağlıdır.
- Plan değişirse veya DWG onaydan sonra değişirse işlem reddedilir.
- Hazır olmayan plan stage edilemez.
- Var olan layout adıyla çakışma blocker üretir; layout ezilmez.
- Eklenti staged DWG'yi çalıştırmadan hemen önce tekrar hash'ler.
- Layout ve viewport değişiklikleri yalnız bellekte tutulur; PDF'den sonra DWG kaydedilmeden
  kapatılır ve final audit için byte-byte aynı kalır.
- Her pafta önce sahipliği belli geçici PDF'ye yazılır; bütün plotlar tamamlanıp staged DWG
  kaydedilmeden kapandıktan sonra final adlar no-overwrite olarak görünür olur. Sonraki terfi
  başarısızsa önceki değişmemiş çıktılar uzunluk ve SHA-256 eşleşmesiyle geri alınır.
- Var olan PDF'nin üstüne yazılmaz; meşgul PlotEngine veya hatalı sayfa ölçüsü işi durdurur.
- `CADPLOT_ENABLE_PUBLISH=1` açıkça verilmedikçe gerçek yayın komutu kapalıdır.
- Çalışma alanı symlink/junction üzerinden yönlendirilemez.
- 19 MCP aracının tamamı kapalı üst-seviye structured-output şeması yayınlar; plan ve receipt
  kimliklerinde kesin digest kalıpları bulunur ve gerçek STDIO `call_tool` testi bu sözleşmeyi sınar.
- Şirket DWT, PC3, PMP, CTB/STB veya DWG dosyaları repoya eklenmez.
- `uv run python scripts/audit-source-tree.py`; tracked veya stage edilebilir CAD/plot dosyalarını,
  arşivleri, yerel config'i, Autodesk DLL'lerini ve yüksek güvenli sır kalıplarını erken reddeder.
- `scripts/run-local-preflight.ps1 -AuditDependencies`; hash zorunlu üretim Python lock'unu,
  transitive .NET paketlerini ve tüm Python lisans beyanlarını güncel ağ veritabanlarıyla denetler.
  Açık veya bilinmeyen lisans varsa kapı kapanır. Sonuç tarama anına aittir; AutoCAD'i açmaz ve
  kalıcı güvenlik ya da canlı plot kanıtı değildir.

## Yerel kurulum

Gereksinimler: Windows, Python 3.11+, `uv` ve canlı inceleme için AutoCAD.

```powershell
cd cadplot-mcp
uv sync --extra autocad --extra dev
Copy-Item examples/config.example.yaml config.yaml
$env:CADPLOT_CONFIG = "$PWD\config.yaml"
uv run cadplot-mcp
```

Şirket yöneticisinin onayladığı Secure MCP Tunnel geliştirici pilotu için aynı 19 araç yüzeyi yalnız
loopback üzerinde Streamable HTTP olarak çalıştırılabilir:

```powershell
uv run cadplot-mcp-http --port 8765
```

Adres `http://127.0.0.1:8765/mcp` olur; Host/Origin koruması ve 1 MiB istek sınırı vardır. Bu komut
kimlik doğrulamalı bir public sunucu değildir; genel amaçlı tünel/reverse proxy ile internete
açılmamalıdır. Ayrıntılar: [loopback Streamable HTTP](docs/loopback-http.md).

Önerilen STDIO tabanlı özel tünel hedefi için, OpenAI'ye bağlanmadan ve AutoCAD'i açmadan gizli
bilgi içermeyen yönetici devir raporu üretilebilir:

```powershell
uv run cadplot-tunnel-preflight --transport stdio
```

Ayrıntılar: [Secure MCP Tunnel yönetici devri](docs/secure-tunnel-handoff.md). Platform tüneli,
runtime anahtarı, workspace yetkileri ve canlı uygulama taraması şirket yöneticisinin kapılarıdır.

MCP istemcisini bağlamadan önce salt-okunur kurulum teşhisini çalıştırabilirsiniz:

```powershell
uv run cadplot-doctor --mode config
uv run cadplot-doctor --mode inspection
uv run cadplot-doctor --mode full
```

`config` yalnız config/yol güvenliğini, `inspection` çalışan AutoCAD COM bağlantısını, `full` ise
yerel named-pipe eklentisini ve güvenilir workspace ayarını da denetler. Komut AutoCAD'i başlatmaz.
Her DWG incelemesi ayrı bir yardımcı süreçte çalışır. `inspection_timeout_seconds` varsayılan 120
saniyedir; takılan COM çağrısı MCP'yi veya batch sayfasını sonsuza kadar bekletmek yerine yalnız o
dosyayı bounded hata yapar. Ayrıntılar: [izole AutoCAD incelemesi](docs/inspection-isolation.md).

İlk ofis envanteri için hiçbir mevcut hedefin üstüne yazmadan boş pilot klasörü oluşturabilirsiniz:

```powershell
.\scripts\new-local-pilot.ps1 -DestinationRoot C:\CadPlotPilot -WhatIf
.\scripts\new-local-pilot.ps1 -DestinationRoot C:\CadPlotPilot
```

Betik yalnız açık kaynak envanter config'ini ve boş `pilot-input`/`pilot-work` klasörlerini oluşturur;
şirket varlığı kopyalamaz ve publish'i açmaz.

`config.yaml` içindeki `allowed_roots`, `workspace_root`, page setup, plotter, plot style, çizim
birimi ve izinli ölçekler ofisin gerçek standardına göre düzenlenmelidir.
Bu adlar henüz bilinmiyorsa gerçek değerleri tahmin etmek yerine
[salt-okunur ofis profili envanteri](docs/office-profile-onboarding.md) ve bilerek hiçbir normal
kâğıt etiketiyle eşleşmeyen `examples/config.inventory.example.yaml` kullanılmalıdır.
Özel PC3 kâğıtlarında `canonical_media`, AutoCAD'in bildirdiği değerle aynı yazım ve büyük/küçük
harf kullanılarak girilmelidir.
`pdf_page_tolerance_mm`, üretilen PDF'nin fiziksel sayfa ölçüsü kontrolünü belirler ve güvenlik
nedeniyle 10 mm'yi geçemez.
Named page setup `PlotType=Layout` ve doğrulanmış 1:1 paper-space plot ölçeği kullanmalıdır.
`Scale to fit` veya okunamayan/custom ama 1:1 olmayan setup planı bloklar; model ölçeği yalnız
kilitli viewport'ta uygulanır.
`minimum_frame_confidence` varsayılan olarak `0.85` değerindedir; nested veya belirsiz çerçeveler
bu eşiğin altında otomatik yayına alınmaz.
Ofisin güvenilir bir çerçeve layer standardı varsa `frame_layers` listesi doldurulabilir; diğer
layer'lardaki adaylar blocker olur.
Her kaynak DWG içinde onaylı bir paper-space title-block layout'u ve tam bir floating viewport
varsa profile `template_layout` eklenebilir. Eklenti layout'u staged kopya içinde klonlar, pafta
geometrisini korur ve klon viewport'unu onaylı pencere/ölçeğe taşır. Eksik veya çok viewport'lu
template işi durdurur.

Onaylı harici DWG/DWT yalnız açık sözleşmeyle kullanılabilir: üst seviyede `template_roots`, profilde
`template_drawing`, gözden geçirilmiş exact `template_sha256` ve `template_layout`. Planlama asset'i
salt-okunur inceler ve layout/page setup/hash kimliğini plan ID'ye bağlar. Staging tekrar hashleyip
yalnız izole job içine kopyalar; eklenti sadece bu kopyayı yeniden hashleyerek import eder ve plot
sonunda staged DWG ile birlikte bütün geçici değişiklikleri kaydetmeden atar. Dosya adına göre arama
veya şirket template klasörüne örtük güven yoktur.

AutoCAD kullanmadan plan→onay→kopya staging→PDF audit zincirini denemek için:

```powershell
uv run python scripts/run-synthetic-demo.py
```

Bu sentetik test gerçek DWG/AutoCAD kabul testi yerine geçmez.
2016 ve 2025 canlı sonuçları ayrı JSON kayıtları olarak tutulur ve
kurulu `cadplot-validate-pilot` komutuyla birlikte doğrulanmadan üretim kabulü verilmez.
Canlı eklenti status'u gömülü build commit'ini ve çalışan adapter DLL SHA-256 değerini verir. Pilot
assembler commit'i operatörden kabul etmek yerine doğrulanmış `bundle-build.json` içinden türetir,
ZIP'in tüm girdilerini yeniden hash'ler ve iki sürümün çalışan binary değerlerini ilgili bundle
adapter'ıyla eşleştirmeden kabul üretmez.
İki pilot geçtikten sonra kurulu `cadplot-acceptance` komutu pilot kanıtını exact release kit/ZIP,
wheel, bundle, build manifest, commit ve sürüme bağlar. Şirket içi koşu ayrıntılarını içermeyen rapor,
şirket yayın izni ile maintainer release onayı ayrı ayrı verilene kadar
`public_release_ready=false` tutar; bkz. [release kabulü](docs/release-acceptance.md).

## Önerilen pilot akışı

Sunumda doğrudan kullanmak için [Pazartesi demo runbook](docs/pazartesi-demo-tr.md) dosyasına bakın.

1. Lisanslı AutoCAD'i açın ve modal pencere bırakmayın.
2. Yalnızca izinli, anonimleştirilmiş bir DWG kopyasını `allowed_roots` altına koyun.
3. `validate_environment` ve `get_autocad_plugin_status` çalıştırın.
4. `inspect_drawing` ile layout/page setup/çerçeve sonuçlarını inceleyin.
5. `create_publish_plan` sonucundaki blocker'ları çözün.
6. `preview_publish_plan` ile aynı hash'li planı eklentiye doğrulatın.
7. Plan kimliğini açıkça onaylayarak `stage_publish_job` çağırın.
8. `validate_staged_job` ile manifesti eklentinin bağımsız workspace ayarına doğrulatın.
9. Lisanslı pilotta aynı `plan_id` ve staging sonucundaki `manifest_sha256` değerlerini
   `queue_publish_job` aracına açıkça verin.
10. `get_publish_job_status` sonucu `Succeeded` olana kadar durumu okuyun.
11. `read_publish_receipt` ile manifest hash'ine bağlı kalıcı başarı kanıtını doğrulayın.
12. Üretilen dosyaları `audit_publish_outputs` ile doğrulayın; ancak hem PDF'ler hem receipt
    geçerliyse dönen `publish_verified=true` sonucunu kabul edin.

300 çizim için `create_batch_publish_plans` aracını varsayılan 20'lik sayfalarla kullanın ve
ilk sayfanın `inventory_id` değerini saklayın. `has_more=false` olana kadar her `next_offset`
çağrısında bu değeri `expected_inventory_id` olarak aynen gönderin; DWG listesi veya metadata
değişirse sayfalama güvenli biçimde durur ve sıfırdan yeniden planlanır.
Hazır planları kopya çalışma alanına almak için en fazla 20 benzersiz `(path, plan_id)` onayını
`stage_publish_batch` aracına verin. Bir dosyanın değişmesi diğer geçerli dosyaları silmez veya
orijinalleri değiştirmez; her sonuç ayrı raporlanır.
Staging sonuçlarındaki benzersiz `(manifest_path, plan_id, manifest_sha256)` üçlülerini yine en
fazla 20'şer adet `queue_publish_batch` çağrısıyla sıraya alın; 300 işi tek bir MCP çağrısına
doldurmayın.
Canlı ilerlemeyi tek tek MCP çağrılarıyla izlemek yerine en fazla 20 benzersiz plan kimliğini
`get_publish_batch_status` aracına verin. Araç job durumlarını ayrı ayrı özetler ve çağrı sonunda
tutarlı tek bir kuyruk kapasitesi örneği döndürür; bu örnek bütün job geçişlerinin aynı anda
görüldüğü anlamına gelmez.
AutoCAD kapanınca canlı kuyruk durumu silinir; terminal sonuçtaki `receipt.json` silinmez.
Yeniden başladıktan sonra `read_publish_receipt` ile kaldığınız işi güvenle doğrulayabilirsiniz.
Tüm çalışma alanını kaldığınız yerden taramak için `create_publish_operations_report` çağrısını
`has_more=false` olana kadar `next_after_job_id` ile sayfalayın. Her `report_page_id` bir kontrol
noktasıdır. Yalnız `awaiting_execution` işlerinde yeniden sıra onayı döner; önce canlı durum bakılır.

Yerel preflight hedef ölçeği ayrıca 300 sentetik kaynakla gerçekten prova eder:

```powershell
uv run python scripts/run-synthetic-batch-demo.py --drawings 300
```

Prova 15 değişmez plan sayfası, 15 onaylı staging batch'i, 300 bağımsız hash'li kopya, tekrarsız
restart raporu ve 300 yapısal PDF audit'i üretir. Bilerek eklenti receipt'i oluşturmaz; bu nedenle
300 çıktının tamamı `manual_review`, `execution_verified=0` ve `publish_verified=0` kalır. Bu kanıt
hedef sayıda yerel orkestrasyon ve fail-closed devam davranışını gösterir; AutoCAD yürütme kanıtı
değildir.

## ChatGPT bağlantısı

Yerel MCP istemcisi, Python sunucusunu `stdio` ile aynı Windows bilgisayarda çalıştırabilir ve
yerel AutoCAD eklentisine named pipe üzerinden ulaşabilir.
Repo içindeki doğrulanmış opsiyonel Codex wrapper'ı
[`integrations/codex/cadplot-mcp`](integrations/codex/cadplot-mcp/README.md) klasöründedir; yalnız
önceden kurulmuş `cadplot-mcp` komutunu başlatır ve şirket dosyalarını paketlemez.
[Dağıtım modları](docs/deployment-modes.md) ve
[ChatGPT bağlantı mimarisi](docs/chatgpt-connection.md), hazır yerel işçi/loopback tünel hedefi ile
henüz uygulanmamış yönetilen HTTPS köprüsünü ayrı teslim kapıları olarak tanımlar.

AutoCAD başlatılmadan önce aynı terminal/başlatıcı ortamında `CADPLOT_WORKSPACE_ROOT`, Python
ayarındaki `workspace_root` ile aynı klasöre ayarlanmalıdır. Eklenti güvenilir workspace değerini
MCP isteğinden kabul etmez.
İnceleme ve staging doğrulaması sırasında publish kapalı bırakılmalıdır. Yalnız yetkili canlı
pilot için AutoCAD başlatılmadan önce `CADPLOT_ENABLE_PUBLISH=1` ayarlanır; değişiklikten sonra
AutoCAD yeniden başlatılır.

`workspace_root`, kaynak `allowed_roots` klasörlerinden tamamen ayrı olmalıdır; iç içe klasörler
reddedilir. Yanlış yazılmış config alanları, boş AutoCAD kaynak adları ve `NaN/Infinity` değerleri
iş başlamadan hata verir.

Gerçek bundle build'i yalnız dosya adlarına güvenmez: `AcMgd.dll`, `AcDbMgd.dll` ve
`AcCoreMgd.dll` assembly kimliklerinin aynı seride olmasını; 2016 için tam `R20.1`, 2025 için tam
`R25.0` gelmesini zorunlu tutar. Yanlış AutoCAD sürümünün klasörü erken reddedilir.

Yetkili operatör; yerel prova, bağımlılık taraması, iki tam-SDK derlemesi, bundle doğrulama,
birleşik release-kit üretimi ve son doğrulamayı tek komutla çalıştırabilir:

```powershell
.\scripts\build-complete-release.ps1 `
  -AutoCAD2016SdkDir "C:\ObjectARX2016\inc" `
  -AutoCAD2025SdkDir "C:\ObjectARX2025\inc" `
  -DotNet "$env:USERPROFILE\.dotnet\dotnet.exe"
```

Bkz. [Autodesk SDK önkoşulları](docs/autodesk-sdk-prerequisites.md). SDK sözleşmesi yetkili
operatöre ait harici bir kapıdır; proje Autodesk geliştirme dosyalarını indirmez, kurmaz, kabul
etmez veya yeniden dağıtmaz.
Build temiz Git commit'i ister, eski artifact'i silmez ve
`artifacts/cadplot-bundle-<sürüm>-<commit>/` altında bundle klasörü, ZIP ve `bundle-build.json`
üretir. Bu manifest commit/sürüm/API kimliği/dosya+ZIP hash'lerini bağlar;
`verify-bundle-release.ps1` ZIP'i çıkarmadan tüm girişleri yeniden doğrular.

Aynı temiz commit için gerçek matching-SDK bundle ve readiness raporu oluştuktan sonra
`scripts/build-release-kit.ps1`; bundle release'i, readiness'e bağlı Python wheel'i, kilit verisini,
`git archive` kaynak kopyasını, güvenli kurulum betiklerini, envanter config'ini, pilot kanıt
komutlarını ve demo runbook'larını tek, üstüne yazılmayan teslim kökünde birleştirir. Kit kendi
hash-bağlı `verify-release-kit.ps1` dosyasını taşıdığı için ayrıca repo checkout'u gerekmez. Bu
doğrulayıcı iki manifesti, tam dosya
ağacını, gömülü bundle/API kanıtını, `uv.lock`-bağlı Python/.NET açık taramasını, eksiksiz Python
lisans envanterini ve dış ZIP'in her girdisini arşivi açmadan doğrular. Kurulum için
[doğrulanmış release-kit rehberine](docs/release-kit-install.md) bakın. Kit içinde
`licensed_live_pilot_ready=false`, `public_release_ready=false` ve `live_publish_proven=false` kalır;
bu durum yalnız ayrı saklanan lisanslı 2016/2025 pilot kanıtıyla değişebilir.
Pilot şeması v4, harici template kullanılmışsa yol bilgisini dışarı vermeden profil/layout/page setup
kimliğini ve onaylı, pilot-sonrası şirket kaynağı, staged-kopya SHA-256 değerlerini birbirine bağlar.
Her run ayrıca yetkili tek sayfalık referans PDF ile üretilen PDF'yi yol sızdırmadan
SHA-256/boyut/sayfa geometrisiyle bağlar; izinli kök dışındaki veya yön/boyutu üretilen PDF'ye uymayan
referansı toplama ve sonraki doğrulamada reddeder, yedi görsel kontrolü tek toplu bayrak yerine ayrı
ayrı açık onaylatır.
Kitteki `install-release-kit.ps1`, `-WhatIf` destekli ve kaldığı yerden devam edebilen tek-komut ilk
kurulum sağlar: yalnız doğrulanan eş bileşenleri yeniden kullanır, pilot/Python'u önce hazırlar ve
bundle'ı en son görünür yapar; AutoCAD'i açmaz, global PATH'i veya publish bayrağını değiştirmez.
`acad.exe` çalışırken bundle mutasyonu reddedilir. Başarılı orkestrasyon; release/Python manifestleri,
bundle hashleri ve yerel yolları bağlayan, üstüne yazılmayan bir kurulum makbuzu bırakır.
Kitteki `install-python.ps1`, tüm transferi yeniden doğrular; frozen lock bağımlılıklarını zorunlu
hashlerle ve wheel'i `--no-deps` ile benzersiz staging venv'e kurup sürüm/commit hedefini atomik
adlandırır. Global PATH'i değiştirmez ve mevcut kurulumu ezmez. `verify-python-install.ps1` wheel,
lock, requirements digest'i, komutlar ve kurulu dağıtım envanterini tekrar denetler.
`uninstall-python.ps1` aynı sürüm/commit ortamını yeniden doğrular, `-WhatIf` ile hedefi gösterir ve
yalnız atomik adı değiştirilmiş karantinayı siler; değiştirilmiş veya yönlendirilmiş klasör korunur.
Küçük yerel demo kiti kendi `verify-demo-kit.ps1` doğrulayıcısını taşır; exact dosya kümesi ve
hashleri kontrol edilirken makineye özel API klasör yolu taşınabilir manifestten çıkarılır.

Yükseltmede AutoCAD'i kapatın; önce `scripts/uninstall-bundle.ps1 -WhatIf` ile tam hedefi görün.
Kurucu bundle'ı önce yüklenmeyen benzersiz bir staging klasörüne kopyalar, kaynak/hedef hash'lerini
eşleştirir ve ancak sonra atomik olarak `CadPlotMcp.bundle` adına taşır; mevcut kurulumu ezmez.
Kopya/doğrulama hatasında staging klasörünü otomatik ve recursive silmez, inceleme için bırakır.
`run-local-preflight.ps1`, protokol-only bir fixture ile `WhatIf → kur → doğrula → WhatIf kaldır →
kaldır` smoke zincirini çalıştırır. Bu test AutoCAD'i açmaz ve canlı uyumluluk kanıtı değildir.
Kaldırıcı yalnız CadPlot MCP adı/ProductCode'u ve tam dosya kümesi doğrulanan paketi kaldırır;
junction veya beklenmeyen dosya üzerinden silmez. Hashleri iki kez doğrular, exact bundle'ı atomik
olarak benzersiz bir non-`.bundle` karantinaya taşır ve recursive silmeyi yalnız bu yolda yapar.

Merkezi ChatGPT Business/Enterprise/Edu ortamında ise şirket yöneticisinin MCP bağlantısını
onaylaması ve merkezi servis ile AutoCAD iş istasyonu arasında yönetilen güvenli bağlantı kurması
gerekir. İnternete named pipe, AutoCAD COM veya korumasız yerel port açılmaz. İlk pilot için yerel
kurulum daha hızlı ve daha düşük risklidir.

## Lisans

MIT. Autodesk ve AutoCAD, Autodesk Inc. markalarıdır. Bu proje Autodesk tarafından üretilmemiş
veya onaylanmamıştır.

Teknik ayrıntılar ve İngilizce dokümantasyon için [README.md](README.md) dosyasına bakın.
