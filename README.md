# FC YBT Raytracer

**TR** | [EN](#english)

FreeCAD için, **harici program gerektirmeyen** (yalnızca FreeCAD'in kendi Python'u ve numpy) bir ışın izleyici **tezgâhı** (workbench): ışık ve kamera nesneleri, malzeme/doku düzenleyici, alan derinliği, Hızlı Render. Tek tuşluk hızlı render için ayrı makro deposuna bakın: [FC_YBTRayTracer](https://github.com/sedatkrn/FC_YBTRayTracer).
Aktif 3B görünümün kamerasından sahneyi render eder, önizlemeyi pencerede gösterir ve PNG olarak kaydeder.

![Arayüz](docs/arayuz.png)

## Özellikler

- Doğrudan ışık, gölge, ortam ışığı ve parlama (Whitted tarzı: gürültüsüz, hızlı)
- En çok 3 ışık: yatay/dikey açı, renk, güç, ışık başına gölge; ortam rengi/şiddeti
- **"FreeCAD ışık ayarlarını kullan"** seçeneği (seçiliyken kendi ışık ayarları devre dışı)
- Saydamlık (cam), Fresnel yansıması ve **cam kırılması** (Snell yasası, kırılma indisi ayarlanır), isteğe bağlı yüzey yansıması
- **Yumuşak yüzeyler**: küre/simit/silindir pürüzsüz gölgelenir, keskin kenarlar korunur
- **Sanal zemin**: sahnenin en altına otomatik yerleşir; renk, opaklık, yansıma ve yükseklik ofseti ayarlanır; **7 hazır doku** (travertin, çakıl, mermer, taş, terrakota, sıva, ahşap) ya da kendi görüntünüz
- Kenar yumuşatma (yalnızca kenar piksellerinde 2x2 / 3x3 / 4x4)
- 4 kalite kademesi (Low, **Hızlı** varsayılan, ...); büyük montajlar için hızlı mod
- Büyük sahnelerde hızlandırma: ekran karoları, otomatik boyutlu ışık ızgarası, küme hiyerarşisi, sahne önbelleği
- Part, Body, App::Part, App::Link, Assembly, BIM (Arch) kapsayıcıları; renkler `ShapeAppearance`'tan
- **Çok çekirdekli render** (Çekirdek: Otomatik/1/2/4; Linux'ta ayrı süreçler, sonuç tek çekirdekle birebir aynı)
- **Mesh nesneleri** (`Mesh::Feature`: STL/OBJ içe aktarma, Şekil→Mesh): üçgenler üçgenleme gerekmeden doğrudan kullanılır; App::Part / Link içinde de doğru konumlanır, malzeme atanabilir
- **Önizlemede tıkla-seç + boya kovası**: render aldıktan sonra önizlemede bir parçaya tıklayın, Malzemeler penceresinde seçilir (Ctrl ile ekleme). "Boya kovası" açıkken tıklanan parçaya düzenlenen malzeme atanır.
- **Benzer nesneler**: aynı şekle sahip nesneler (kopyalar, Link'ler, konumu/dönmesi farklı olsa da) listede ×N olarak işaretlenir; "Benzerleri birlikte seç" ile cıvata gibi çok kopyalı parçalara tek hamlede malzeme atanır / dahil-hariç yapılır.
- **"Render'a dahil et"** kutusu: Malzemeler (Surface) penceresindeki nesne listesinde her nesnenin onay kutusu; kutusu kaldırılan nesne sahneye hiç girmez (gölge/yansıma da vermez)
- **"Kadraj dışındaki nesneleri atla"** seçeneği: kameranın görmediği nesneler üçgenlenmez ve sahne boyutuna katılmaz (büyük modelde yakın plan çekimlerde hızlandırır)
- **Arayüzsüz API** (`ybt_raytracer.api`): panel açmadan render, **turntable** ve hareketli parça **animasyonu** (aşağıya bakın)
- **Ortam / HDRI**: hazır gökyüzü ya da `.hdr` / PNG / JPG (equirectangular) harita; arka plan, yansıma ve gökyüzü aydınlatması (pencereden giren ışık dahil), haritadaki güneş otomatik gölgeli ışık olur; şiddet, dönüş, ışın sayısı
- **Malzemeler (Surface) penceresi**: renk, diffuse, specular, glossiness, metal, yansıma, saydamlık, kırılma indisi, parlama (luminosity), bump; hazır malzeme kütüphanesi, kendi malzemelerin, nesne listesinde sağ tık kopyala/yapıştır, boya kovası
- **LightWave tarzı Doku Düzenleyici**: her kanal için çok katmanlı doku (görüntü ya da prosedürel), blend modları, projeksiyon (küp / düzlemsel / silindirik / küresel), ölçek-konum-dönme, otomatik boyutlandır, döşeme modu (otomatik / tekrar / ayna), katman önizlemesi
- **Prosedürel dokular**: türbülans, fraktal, damar, mermer, ahşap, dama, ızgara, nokta, tuğla, dalga (görüntü gerekmez)
- **Belgeyle saklama**: panel ayarları ve kullanılan malzeme tanımları FCStd içine yazılır; belge başka bilgisayarda açılınca geri gelir
- Ayar paneli ile açılan pencere, alt kısımda ilerleme çubuğu, durdurma, farklı kaydet; pencereler görev çubuğunda görünür ve küçültülebilir

![Sanal zemin ve ayar paneli](docs/sanal_zemin.png)
![Cam ve yansıma](docs/cam_yansima.png)
![Zemin dokuları](docs/floor_textures.png)
![Yumuşak yüzeyler: önce / sonra](docs/smooth_shading.png)
![Cam kırılması: IOR kapalı / 1.5](docs/refraction.png)
![Işıklar](docs/bim_isiklar.png)

### Ortam / HDRI

| HDRI gökyüzü | Standart ışıklar |
|---|---|
| ![HDRI ile bahçe](docs/hdri_ev.png) | ![Standart ışıklarla bahçe](docs/sky_ev_standart.png) |
| ![HDRI ile oda](docs/hdri_oda.png) | ![Standart ışıklarla oda](docs/oda_standart.png) |

![Ortam / HDRI ayarları](docs/panel_hdri.jpg)

### Malzemeler ve dokular

![Malzeme penceresi](docs/surface_editor.jpg)
![Doku düzenleyici](docs/texture_editor.jpg)

## Dil / Language
Panelin sağ üstündeki "Dil / Language" kutusundan Türkçe veya English seçilir; seçim hatırlanır (ilk açılışta FreeCAD arayüz dili Türkçeyse Türkçe, değilse English).
The UI language (Türkçe / English) is chosen at the top-right of the panel; it is remembered (first run follows FreeCAD's locale).

## İçerik klasörü

**⚙ Seçenekler…** penceresinin **Genel** sekmesinde tek bir **İçerik klasörü** seçilir (LightWave'deki Content Directory gibi); altında sabit adlı alt klasörler otomatik oluşturulur:
`materials/` (malzeme kütüphanesi), `textures/` (dokular), `hdri/`, `settings/` (kayıtlı varsayılanlar), `renders/` (render çıktıları; istenirse çıktılar belgenin yanına da yazılabilir).
Dosya seçiciler bu klasörlerden açılır, `textures/` ve `hdri/` içindeki dosyalar listelerde görünür. Belgeye gömülü bir doku ya da HDRI yolu bu bilgisayarda yoksa aynı dosya adı ilgili klasörde aranır (belgeyi başka bilgisayara taşırken işe yarar).
İçerik klasörü ilk kez seçilince eski yerdeki malzeme kütüphanesi ve varsayılanlar yeni klasöre kopyalanır (eskiler silinmez). Ayar makineye özgüdür, belgeye yazılmaz; seçilmezse her şey eski yerlerinde kalır.

## Kurulum

**Eklenti olarak (önerilen):** depoyu FreeCAD'in `Mod/` klasörüne kopyalayın (Linux: `~/.local/share/FreeCAD/Mod/FC_YBTRayTracer`; sürüme göre `v1-1/Mod/` olabilir)
ve FreeCAD'i yeniden başlatın. Tezgâh listesinde kendi ikonuyla **YBT Raytracer** tezgâhı çıkar; araç çubuğunda ve menüsünde üç komut vardır: **YBT Raytracer** (ayar paneli), **YBT Malzemeler**, **YBT Varsayılanlar**.
Aynı komutlar her tezgâhta *Macro* menüsünde ve *Macro* araç çubuğunda da bulunur (araç çubuğu gizliyse *View > Toolbars > Macro*).
Eski makro kopyası makro klasöründe duruyorsa silin (iki kopya karışmasın).
Ayarlar *Edit > Preferences > Advanced* (parametre düzenleyici) içinde `User parameter:BaseApp/Preferences/Mod/FCYBTRayTracer` altındadır:
`SeparateWorkbench` (Boolean, varsayılan true) = false yapılırsa ayrı tezgâh gösterilmez; `ShowInAllWorkbenches` (Boolean, varsayılan true) = false yapılırsa Macro araç çubuğu/menüsüne eklenmez. Değişiklik için FreeCAD'i yeniden başlatın.


Pencere açıkken 3B görünümü çevirip tekrar **Render** alabilirsiniz (sahne önbelleğe alınır).
Sonuç PNG olarak kaydedilir; klasör yazılamazsa `~/Pictures` veya ev dizini kullanılır.

## Gereksinimler

FreeCAD 1.0 veya üstü (1.1 / 26.x ile denendi), numpy (FreeCAD ile birlikte gelir). Başka zorunlu bağımlılık yoktur.

**Hızlandırma (isteğe bağlı):** [numba](https://numba.pydata.org/) kuruluysa gölge, AO, GI ve yansıma ışınlarının
araması derlenir; render genelde **2-3 kat hızlanır**, görüntü piksel piksel aynı kalır (kafe sahnesi, 94 bin üçgen:
13,8 sn -> 6,2 sn). Bir kez *Seçenekler > Genel > Hızlandırıcı > Numba'yı kur* ile kurulur (internet gerekir, ~45 MB).
`<FreeCAD kullanıcı verisi>/YBTRayTracer/pylibs` klasörüne gider; FreeCAD'in kendi Python'una dokunulmaz.
numba kurulmazsa her şey numpy ile çalışmaya devam eder. FreeCAD'in Python sürümü için numba paketi bulunmalıdır
(FreeCAD 26.x / Python 3.13, numba 0.68 ile çalışıyor).

## Arayüzsüz kullanım ve animasyon

Panel açmadan, makrodan ya da FreeCAD Python konsolundan:

```python
from ybt_raytracer import api
api.render_document(App.ActiveDocument, "/tmp/sahne.png", width=1280, floor=True, aa=3)

# turntable: kamera sahnenin çevresinde döner (sahne bir kez hazırlanır)
doc = App.ActiveDocument
scene = api.prepare_scene(doc)
for i, cam in enumerate(api.orbit_cameras(scene, frames=72, elevation=20)):
    api.render_document(doc, "/tmp/kare_%04d.png" % i, width=960, camera=cam, scene=scene)

# hareketli parçalar: şekiller bir kez üçgenlenir, her karede yalnız yerleşim uygulanır
cache = {}
for i in range(72):
    nesne.Placement = ...; doc.recompute()
    api.render_document(doc, "/tmp/kare_%04d.png" % i, width=960, camera=cam, tess_cache=cache)
```

Ayarlar `settings.py` adlarıyla (küçük harf) anahtar sözcük olarak verilir ve yalnız o çağrı için geçerlidir (`floor=True, shadows=False, aa=4, tess_factor=0.002, cull_offscreen=True ...`).
Kamera: `api.make_camera(pos, target, fov=40)`, `api.fit_camera(scene)`, `api.orbit_cameras(...)`. Arayüzsüz FreeCAD'de (`freecadcmd`) kamera verilmezse sahneyi sığdıran izometrik kamera kullanılır.
Şekil parametreleri değişirse `tess_cache` için yeni bir sözlük verin. Hazır örnek: [`examples/FC_ybt_turntable.FCMacro`](examples/FC_ybt_turntable.FCMacro) (kamera turntable'ı ya da dönen parça; kare dizisi → `ffmpeg` ile video).

## Sınırlar

- Mesh nesnelerinde köşe renkleri (vertex colors) kullanılmaz; nesnenin tek rengi/malzemesi uygulanır.
- Kırılma yalnızca saydam yüzeylerde ve kapalı gövdelerde (küre, silindir, kutu) doğru çalışır; cam altında ışık odaklanması (kostik) yok, gölge camdan yarı saydam geçer.
- Zemin dokularının bir kısmı (çakıl, mermer, taş, ahşap) kenarlardan aynalanarak tekrarlanır; geniş zeminde simetri fark edilebilir.
- Sahne hazırlığı (üçgenleme, karo/ızgara/küme ağacı) tek çekirdekte; çok büyük sahnelerde cam/yansıma yavaştır (düşük kalite kademesi önerilir).
- Sekmeli (dolaylı) ışık henüz yok: yalnız pencereden/ışıktan doğrudan gelen aydınlatma hesaplanır; kapalı iç mekânlarda ışık almayan duvarlar koyu kalır (ortam ışığı dolgu olarak kullanılabilir).
- HDRI dosyaları büyükse (8k) ilk okuma birkaç saniye sürer; küçültülmüş hali önbelleğe alınır. Düşük dinamik aralıklı haritalarda güneş ayrılamaz.

## Yol haritası

Sekmeli (dolaylı) aydınlatma (irradiance cache + Monte Carlo), son kalite yol izleme modu, yüzey özellikleri (çift taraflı, renk filtresi, pürüzlü yansıma, clearcoat), FreeCAD eklentisine (addon) dönüştürme, daha hızlı sahne hazırlığı.

## Lisans

MIT, bkz. [LICENSE](LICENSE).

---

## English

A fast, **dependency-free** ray-tracer **workbench** for FreeCAD (just FreeCAD's own Python and numpy): light and camera objects, material/texture editor, depth of field, Quick Render. For the one-click quick-render macro see the separate [FC_YBTRayTracer](https://github.com/sedatkrn/FC_YBTRayTracer) repository. It renders the active 3D view from its camera, shows a live preview and saves a PNG.

### Features

- Direct light, shadows, ambient light and specular highlights (Whitted style: noise-free and fast)
- Up to 3 lights: horizontal/vertical angle, colour, power, per-light shadows; ambient colour/intensity
- **"Use FreeCAD light settings"** option (disables the macro's own lights when checked)
- Transparency (glass) with Fresnel reflection and **refraction** (Snell's law, adjustable IOR), optional surface reflection
- **Smooth surfaces**: spheres/tori/cylinders shaded smoothly, sharp edges preserved
- **Virtual floor**: placed at the scene's lowest point automatically; colour, opacity, reflection and height offset adjustable; **7 built-in textures** (travertine, gravel, marble, stone, terracotta, plaster, wood) or your own image
- Edge-only anti-aliasing (2x2 / 3x3 / 4x4)
- 4 quality levels (Low, **Fast** default, ...), suited to large assemblies
- Large-scene acceleration: screen tiles, auto-sized light-space grid, cluster hierarchy, scene cache
- Part, Body, App::Part, App::Link, Assembly and BIM (Arch) containers; colours from `ShapeAppearance`
- **Multi-core rendering** (Cores: Auto/1/2/4; output identical to single-core)
- **Mesh objects** (`Mesh::Feature`: STL/OBJ import, Shape→Mesh): triangles are used directly, no tessellation; correctly placed inside App::Part / Link; materials can be assigned
- **"Include in render"** checkbox per object in the Materials (Surface) window's object list; unchecked objects are left out of the scene entirely (no shadows/reflections either)
- **"Skip objects outside the frame"** option: objects the camera cannot see are not tessellated and do not count toward the scene size (speeds up close-ups of large models)
- **Headless API** (`ybt_raytracer.api`): render without opening the panel, **turntable** and moving-part **animation** (see below)
- **Environment / HDRI**: built-in sky or an equirectangular `.hdr` / PNG / JPG map; background, reflections and sky lighting (including light entering through windows); the sun in the map becomes a shadow-casting light; intensity, rotation, ray count
- **Materials (Surface) window**: colour, diffuse, specular, glossiness, metal, reflection, transparency, IOR, luminosity, bump; built-in library, your own materials, right-click copy/paste on the object list, paint bucket
- **LightWave-style Texture Editor**: multi-layer textures per channel (image or procedural), blend modes, projection (cubic / planar / cylindrical / spherical), scale-position-rotation, automatic sizing, tiling mode (auto / repeat / mirror), layer preview
- **Procedural textures**: turbulence, fractal noise, veins, marble, wood, checker, grid, dots, bricks, ripples (no image needed)
- **Stored with the document**: panel settings and the definitions of the materials in use are saved inside the FCStd and restored on any machine
- Settings panel opens first; progress bar, stop button, save-as; windows show in the taskbar and can be minimised

### Content directory

In the **General** tab of **⚙ Options…** you pick one **Content directory** (like LightWave's); fixed sub-folders are created under it:
`materials/` (material library), `textures/`, `hdri/`, `settings/` (saved defaults), `renders/` (render outputs; they can still go next to the document instead).
File pickers open in these folders and the files in `textures/` and `hdri/` show up in the lists. If a texture or HDRI path stored in the document does not exist on this computer, a file with the same name is looked up in the matching folder.
The first time a content directory is chosen, the material library and defaults from the old location are copied into it (originals are kept). The setting is per machine and is not written into the document; if unset, everything stays where it was.

### Install

**As an add-on (recommended):** copy the repository into FreeCAD's `Mod/` folder (Linux: `~/.local/share/FreeCAD/Mod/FC_YBTRayTracer`, or `v1-1/Mod/` depending on the version) and restart FreeCAD.
A **YBT Raytracer** workbench with its own icon, toolbar and menu appears in the workbench list; it holds three commands: **YBT Raytracer** (settings panel), **YBT Materials**, **YBT Options**.
The same commands are also in the *Macro* menu and *Macro* toolbar of every workbench (if the toolbar is hidden: *View > Toolbars > Macro*).
Remove any old macro copy from the macro folder so the two copies don't clash.
Options live in the parameter editor under `User parameter:BaseApp/Preferences/Mod/FCYBTRayTracer`:
`SeparateWorkbench` (Boolean, default true) = false hides the separate workbench; `ShowInAllWorkbenches` (Boolean, default true) = false stops adding the commands to the Macro toolbar/menu. Restart FreeCAD after changing either.


You can rotate the 3D view while the window is open and press **Render** again; the scene is cached.

### Requirements

FreeCAD 1.0+ (tested with 1.1 / 26.x) and numpy (bundled with FreeCAD).

**Acceleration (optional):** with [numba](https://numba.pydata.org/) installed, the search for shadow, AO, GI and
reflection rays is compiled and renders are usually **2-3x faster** with a pixel-identical image (cafe scene,
94k triangles: 13.8 s -> 6.2 s). Install it once from *Options > General > Accelerator > Install numba*
(needs internet, ~45 MB). It goes into `<FreeCAD user data>/YBTRayTracer/pylibs`; FreeCAD's own Python is not touched.
Without numba everything still works with numpy. numba wheels must exist for FreeCAD's Python version
(FreeCAD 26.x / Python 3.13 works with numba 0.68).

### Headless API and animation

Without opening the panel, from a macro or FreeCAD's Python console:

```python
from ybt_raytracer import api
api.render_document(App.ActiveDocument, "/tmp/scene.png", width=1280, floor=True, aa=3)

# turntable: the camera orbits the scene (scene is prepared once)
doc = App.ActiveDocument
scene = api.prepare_scene(doc)
for i, cam in enumerate(api.orbit_cameras(scene, frames=72, elevation=20)):
    api.render_document(doc, "/tmp/frame_%04d.png" % i, width=960, camera=cam, scene=scene)

# moving parts: shapes are tessellated once, only their placement is applied each frame
cache = {}
for i in range(72):
    obj.Placement = ...; doc.recompute()
    api.render_document(doc, "/tmp/frame_%04d.png" % i, width=960, camera=cam, tess_cache=cache)
```

Settings are passed as keyword arguments named like `settings.py` (lower case) and apply to that call only (`floor=True, shadows=False, aa=4, tess_factor=0.002, cull_offscreen=True ...`).
Cameras: `api.make_camera(pos, target, fov=40)`, `api.fit_camera(scene)`, `api.orbit_cameras(...)`. In headless FreeCAD (`freecadcmd`) an isometric camera fitting the scene is used when no camera is given.
Pass a fresh dict as `tess_cache` when shape parameters change. Ready-made example: [`examples/FC_ybt_turntable.FCMacro`](examples/FC_ybt_turntable.FCMacro) (camera turntable or rotating part; frame sequence → video with `ffmpeg`).

### Screenshots

| HDRI sky | Standard lights |
|---|---|
| ![Garden with HDRI](docs/hdri_ev.png) | ![Garden with standard lights](docs/sky_ev_standart.png) |
| ![Room with HDRI](docs/hdri_oda.png) | ![Room with standard lights](docs/oda_standart.png) |

![Environment / HDRI settings](docs/panel_hdri.jpg)
![Material window](docs/surface_editor.jpg)
![Texture editor](docs/texture_editor.jpg)

### Limitations

- Mesh vertex colors are not used; the object's single colour/material applies.
- Refraction works best on transparent closed bodies (sphere, cylinder, box); no caustics, shadows pass through glass as partial shade.
- Some floor textures (gravel, marble, stone, wood) tile by mirroring, so symmetry may show on a large floor.
- Scene preparation (tessellation, tile/grid/cluster build) is single-threaded; glass/reflections on very large scenes are slow (use a lower quality level).
- No bounced (indirect) light yet: only direct light from lights/windows is computed, so walls that receive no light stay dark in closed interiors (use ambient light as fill).
- Large (8k) HDRI files take a few seconds on first load; a reduced copy is cached. The sun cannot be extracted from low-dynamic-range maps.

### Roadmap

Bounced (indirect) lighting (irradiance cache + Monte Carlo), a final-quality path-tracing mode, surface features (double-sided, colour filter, glossy reflection, clearcoat), conversion into a FreeCAD addon, faster scene preparation.

### License

MIT, see [LICENSE](LICENSE).


## Zemin dokuları / Floor textures

Sanal zemin için hazır dokular: travertin, çakıl, beyaz mermer, taş kaplama, terrakota, sıva, ahşap; ayrıca *Dosyadan…* ile kendi PNG/JPG görüntünüz. *Doku boyu* ile ölçek ayarlanır.
Ready-made floor textures (7) or your own image via *Dosyadan…*; *Doku boyu* sets the scale.

## Kod yapısı / Code layout

```
ybt_raytracer/
  settings.py        ayarlar / settings
  core.py            Scene + kesişim / intersection
  parallel.py        BLAS sınırı, iptal, ilerleme / BLAS limit, cancel, ticker
  accel.py           karo, ışık ızgarası, küme hızlandırıcılar / accelerators
  render.py          ışın izleme döngüsü / ray-tracing loop
  output.py          PNG yazıcı / PNG writer
  freecad_scene.py   FreeCAD -> üçgen, kamera, ışıklar / FreeCAD scene import
  ui.py              ayar paneli / settings panel
  materials.py       malzeme kütüphanesi, dahil et işareti / materials, include flag
  surface_ui.py      malzeme penceresi / material window
  api.py             arayüzsüz API, animasyon / headless API, animation
examples/
  FC_ybt_turntable.FCMacro   animasyon örneği / animation example
CHANGELOG.md
```

## Doku lisansı / Texture licence

`ybt_raytracer/textures/mimari_*.png` (mimari malzemeler): CC BY 4.0, ayrıntı `ybt_raytracer/textures/LICENSE-TEXTURES.txt`.

## Simge atıfları / Icon credits

- `ybt_raytracer/icons/godot_render.png`: Godot Engine logosu, © Godot Engine contributors, [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). / Godot Engine logo, © Godot Engine contributors, CC BY 4.0.
- `ybt_raytracer/icons/cycles_render.png`: Blender logosu, © Blender Foundation; Blender'ın logo kullanım koşulları geçerlidir. Bu proje Blender Foundation ile bağlantılı değildir. / Blender logo, © Blender Foundation, used under the Blender logo usage terms. This project is not affiliated with the Blender Foundation.
