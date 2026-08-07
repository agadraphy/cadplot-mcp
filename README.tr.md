# CadPlot MCP

CadPlot MCP; çok sayıdaki revize DWG dosyasını denetlenebilir biçimde incelemek, pafta
çerçevelerini şirket page setup'larıyla eşleştirmek, ölçek/layout kararlarını planlamak ve PDF
çıktılarını doğrulamak için geliştirilen açık kaynak bir MCP sunucusudur.

## Mevcut durum

Hazır olan parçalar:

- yalnızca izin verilen klasörlerde DWG tarama;
- çalışan AutoCAD üzerinden salt-okunur layout, page setup ve çerçeve inceleme;
- `70x100`, `700x1000 mm` ve `A0-A5` gibi kâğıt etiketi tanıma;
- çerçeve geometrisinden plot penceresi, yön ve izinli ölçek türetme;
- PC3 ile CTB/STB değerlerini şirket profiliyle karşılaştırma;
- hash'li ve onay kapılı dry-run planı;
- 50 DWG'ye kadar sayfalı, kaldığı yerden devam edebilen batch planlama;
- orijinale dokunmadan izole çalışma klasörüne doğrulanmış DWG kopyası alma;
- beklenen PDF'leri yol, gerçek PDF yapısı, tek sayfa, sayfa ölçüsü, boyut ve SHA-256 açısından
  denetleme;
- AutoCAD 2016 ve 2025-2026 için ayrı .NET adaptör/bundle iskeleti.

Henüz tamamlanmamış kapı: gerçek layout oluşturma ve PDF plot işlemi, Autodesk SDK referanslarıyla
derlenip lisanslı AutoCAD 2016/2025 üzerinde örnek DWG ve şirket plot kaynaklarıyla kabul testinden
geçmelidir. Bu doğrulama yapılmadan proje üretim-hazır olarak sunulmaz.

## Güvenlik modeli

- Keyfi AutoLISP veya AutoCAD komutu çalıştırılmaz.
- Kaynak DWG'nin üstüne yazılmaz, dosya taşınmaz veya silinmez.
- Her plan kaynak DWG'nin SHA-256 parmak izine bağlıdır.
- Plan değişirse veya DWG onaydan sonra değişirse işlem reddedilir.
- Hazır olmayan plan stage edilemez.
- Var olan layout adıyla çakışma blocker üretir; layout ezilmez.
- Çalışma alanı symlink/junction üzerinden yönlendirilemez.
- Şirket DWT, PC3, PMP, CTB/STB veya DWG dosyaları repoya eklenmez.

## Yerel kurulum

Gereksinimler: Windows, Python 3.11+, `uv` ve canlı inceleme için AutoCAD.

```powershell
git clone https://github.com/YOUR-USER/cadplot-mcp.git
cd cadplot-mcp
uv sync --extra autocad --extra dev
Copy-Item examples/config.example.yaml config.yaml
$env:CADPLOT_CONFIG = "$PWD\config.yaml"
uv run cadplot-mcp
```

`config.yaml` içindeki `allowed_roots`, `workspace_root`, page setup, plotter, plot style, çizim
birimi ve izinli ölçekler ofisin gerçek standardına göre düzenlenmelidir.
Özel PC3 kâğıtlarında `canonical_media`, AutoCAD'in bildirdiği değerle aynı yazım ve büyük/küçük
harf kullanılarak girilmelidir.
`pdf_page_tolerance_mm`, üretilen PDF'nin fiziksel sayfa ölçüsü kontrolünü belirler ve güvenlik
nedeniyle 10 mm'yi geçemez.
`minimum_frame_confidence` varsayılan olarak `0.85` değerindedir; nested veya belirsiz çerçeveler
bu eşiğin altında otomatik yayına alınmaz.

## Önerilen pilot akışı

1. Lisanslı AutoCAD'i açın ve modal pencere bırakmayın.
2. Yalnızca izinli, anonimleştirilmiş bir DWG kopyasını `allowed_roots` altına koyun.
3. `validate_environment` ve `get_autocad_plugin_status` çalıştırın.
4. `inspect_drawing` ile layout/page setup/çerçeve sonuçlarını inceleyin.
5. `create_publish_plan` sonucundaki blocker'ları çözün.
6. `preview_publish_plan` ile aynı hash'li planı eklentiye doğrulatın.
7. Plan kimliğini açıkça onaylayarak `stage_publish_job` çağırın.
8. `validate_staged_job` ile manifesti eklentinin bağımsız workspace ayarına doğrulatın.
9. Gerçek publisher tamamlandığında üretilen dosyaları `audit_publish_outputs` ile doğrulayın.

300 çizim için `create_batch_publish_plans` aracını varsayılan 20'lik sayfalarla kullanın ve
`has_more=false` olana kadar dönen `next_offset` değeriyle devam edin.
Hazır planları kopya çalışma alanına almak için en fazla 20 benzersiz `(path, plan_id)` onayını
`stage_publish_batch` aracına verin. Bir dosyanın değişmesi diğer geçerli dosyaları silmez veya
orijinalleri değiştirmez; her sonuç ayrı raporlanır.

## ChatGPT bağlantısı

Yerel MCP istemcisi, Python sunucusunu `stdio` ile aynı Windows bilgisayarda çalıştırabilir ve
yerel AutoCAD eklentisine named pipe üzerinden ulaşabilir.

AutoCAD başlatılmadan önce aynı terminal/başlatıcı ortamında `CADPLOT_WORKSPACE_ROOT`, Python
ayarındaki `workspace_root` ile aynı klasöre ayarlanmalıdır. Eklenti güvenilir workspace değerini
MCP isteğinden kabul etmez.

Merkezi ChatGPT Business/Enterprise/Edu ortamında ise şirket yöneticisinin MCP bağlantısını
onaylaması ve merkezi servis ile AutoCAD iş istasyonu arasında yönetilen güvenli bağlantı kurması
gerekir. İnternete named pipe, AutoCAD COM veya korumasız yerel port açılmaz. İlk pilot için yerel
kurulum daha hızlı ve daha düşük risklidir.

## Lisans

MIT. Autodesk ve AutoCAD, Autodesk Inc. markalarıdır. Bu proje Autodesk tarafından üretilmemiş
veya onaylanmamıştır.

Teknik ayrıntılar ve İngilizce dokümantasyon için [README.md](README.md) dosyasına bakın.
