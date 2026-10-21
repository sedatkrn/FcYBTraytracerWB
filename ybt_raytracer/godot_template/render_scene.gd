extends SceneTree
# FC YBT Raytracer -> Godot köprüsü: scene.json + scene.glb dosyalarından sahne kurar, bir kare render edip PNG yazar.
# Kullanım: godot --path <klasör> -s res://render_scene.gd -- scene.json scene.glb cikti.png

const WARM_FRAMES := 10

var _frames := 0
var _out := ""
var _size := Vector2i(1280, 720)
var _ss := 1


func _fail(msg: String) -> void:
	push_error(msg)
	printerr("YBT_ERROR: " + msg)
	quit(2)


func _initialize() -> void:
	RenderingServer.directional_soft_shadow_filter_set_quality(RenderingServer.SHADOW_QUALITY_SOFT_HIGH)
	RenderingServer.positional_soft_shadow_filter_set_quality(RenderingServer.SHADOW_QUALITY_SOFT_HIGH)
	var args := OS.get_cmdline_user_args()
	if args.size() < 3:
		_fail("kullanim: -- scene.json scene.glb cikti.png")
		return
	_out = args[2]
	var txt := FileAccess.get_file_as_string(args[0])
	var cfg = JSON.parse_string(txt)
	if typeof(cfg) != TYPE_DICTIONARY:
		_fail("scene.json okunamadi: " + args[0])
		return
	_size = Vector2i(int(cfg["size"][0]), int(cfg["size"][1]))
	_ss = int(cfg.get("supersample", 1))
	var k: float = cfg["scale"]

	# --- geometri (glb, çalışma zamanında yüklenir)
	var doc := GLTFDocument.new()
	var state := GLTFState.new()
	var err := doc.append_from_file(args[1], state)
	if err != OK:
		_fail("glb yuklenemedi (%d): %s" % [err, args[1]])
		return
	var model: Node = doc.generate_scene(state)
	var holder := Node3D.new()
	holder.scale = Vector3.ONE * k
	holder.add_child(model)
	root.add_child(holder)

	var win := root
	win.size = _size * _ss
	win.msaa_3d = Viewport.MSAA_4X
	win.use_debanding = true

	_make_environment(cfg)
	_make_camera(cfg)
	for L in cfg["lights"]:
		_make_light(L)
	if cfg["floor"]["on"]:
		_make_floor(cfg["floor"])


func _col(a) -> Color:
	return Color(a[0], a[1], a[2])


func _make_environment(cfg: Dictionary) -> void:
	var e: Dictionary = cfg["env"]
	var env := Environment.new()
	var sky := Sky.new()
	var hdri: String = e.get("hdri", "")
	var have_hdri := false
	if hdri != "" and FileAccess.file_exists(hdri):
		var img := Image.load_from_file(hdri)
		if img != null and not img.is_empty():
			var pm := PanoramaSkyMaterial.new()
			pm.panorama = ImageTexture.create_from_image(img)
			pm.energy_multiplier = e["hdri_energy"]
			sky.sky_material = pm
			have_hdri = true
	if not have_hdri:
		var pm2 := ProceduralSkyMaterial.new()
		pm2.sky_top_color = _col(e["top"])
		pm2.sky_horizon_color = _col(e["bottom"])
		pm2.ground_horizon_color = _col(e["bottom"])
		pm2.ground_bottom_color = _col(e["bottom"]) * 0.8
		pm2.sky_curve = 0.25
		sky.sky_material = pm2
	env.sky = sky
	if have_hdri and e.get("show_bg", true):
		env.background_mode = Environment.BG_SKY
	else:
		# Işın izleyicideki gibi ekranda yukarıdan aşağıya renk geçişi (HDRI yoksa); yansımalar yine gökyüzünden gelir
		env.background_mode = Environment.BG_CANVAS
		var layer := CanvasLayer.new()
		layer.layer = -100
		var grad := Gradient.new()
		grad.set_color(0, _col(e["top"]))
		grad.set_color(1, _col(e["bottom"]))
		var gt := GradientTexture2D.new()
		gt.gradient = grad
		gt.fill_from = Vector2(0, 0)
		gt.fill_to = Vector2(0, 1)
		gt.width = 4
		gt.height = 256
		var tr := TextureRect.new()
		tr.texture = gt
		tr.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
		tr.stretch_mode = TextureRect.STRETCH_SCALE
		tr.set_anchors_preset(Control.PRESET_FULL_RECT)
		layer.add_child(tr)
		root.add_child(layer)
	if have_hdri:
		env.sky_rotation = Vector3(0, deg_to_rad(float(e.get("hdri_rot", 0.0))), 0)
	# Işın izleyicideki gibi düz (renkli) ortam ışığı; yansımalar gökyüzünden gelir
	env.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	env.ambient_light_color = _col(e["ambient_color"])
	env.ambient_light_energy = clampf(float(e["ambient"]), 0.0, 4.0)
	env.reflected_light_source = Environment.REFLECTION_SOURCE_SKY
	match String(cfg.get("tonemap", "linear")):
		"aces":
			env.tonemap_mode = Environment.TONE_MAPPER_ACES
		"reinhard":
			env.tonemap_mode = Environment.TONE_MAPPER_REINHARDT
		_:
			env.tonemap_mode = Environment.TONE_MAPPER_LINEAR
	env.tonemap_exposure = cfg.get("exposure", 1.0)
	# Vulkan (forward_plus) modunda: ekran uzayı AO ve yansıma; gl_compatibility bunları yok sayar
	env.ssao_enabled = true
	env.ssao_radius = 0.35
	env.ssao_intensity = 1.6
	env.ssao_power = 1.5
	env.ssao_light_affect = 0.4
	env.ssr_enabled = true
	env.ssr_max_steps = 96
	env.ssr_fade_in = 0.1
	env.ssr_fade_out = 2.0
	var we := WorldEnvironment.new()
	we.environment = env
	root.add_child(we)


func _make_camera(cfg: Dictionary) -> void:
	var c: Dictionary = cfg["camera"]
	var cam := Camera3D.new()
	var fwd := Vector3(c["fwd"][0], c["fwd"][1], c["fwd"][2]).normalized()
	var up := Vector3(c["up"][0], c["up"][1], c["up"][2]).normalized()
	var pos := Vector3(c["pos"][0], c["pos"][1], c["pos"][2])
	cam.transform = Transform3D(Basis.looking_at(fwd, up), pos)
	cam.keep_aspect = Camera3D.KEEP_HEIGHT if c["keep"] == "height" else Camera3D.KEEP_WIDTH
	if c["persp"]:
		cam.projection = Camera3D.PROJECTION_PERSPECTIVE
		cam.fov = c["fov"]
	else:
		cam.projection = Camera3D.PROJECTION_ORTHOGONAL
		cam.size = c["size"]
	cam.near = 0.05
	cam.far = 400.0
	root.add_child(cam)
	cam.make_current()


func _make_light(L: Dictionary) -> void:
	var kind: String = L["kind"]
	var light: Light3D
	if kind == "distant":
		var d := DirectionalLight3D.new()
		d.light_angular_distance = float(L.get("angular", 0.5))
		d.directional_shadow_max_distance = 16.0
		d.directional_shadow_mode = DirectionalLight3D.SHADOW_ORTHOGONAL
		light = d
	elif kind == "spot":
		var s := SpotLight3D.new()
		s.light_size = float(L.get("size", 0.0))
		s.spot_range = L["range"]
		s.spot_angle = L["angle"]
		s.spot_angle_attenuation = L["angle_atten"]
		s.spot_attenuation = L.get("atten", 1.0)
		light = s
	else:
		var o := OmniLight3D.new()
		o.light_size = float(L.get("size", 0.0))
		o.omni_range = L["range"]
		o.omni_attenuation = L.get("atten", 1.0)
		light = o
	light.light_color = _col(L["color"])
	light.light_energy = L["energy"]
	light.shadow_enabled = L["shadow"]
	light.shadow_bias = 0.04
	light.shadow_normal_bias = 2.0
	root.add_child(light)
	if kind == "distant" or kind == "spot":
		var dir := Vector3(L["dir"][0], L["dir"][1], L["dir"][2]).normalized()
		var upv := Vector3.UP if absf(dir.dot(Vector3.UP)) < 0.99 else Vector3.RIGHT
		var pos := Vector3.ZERO if kind == "distant" else Vector3(L["pos"][0], L["pos"][1], L["pos"][2])
		light.transform = Transform3D(Basis.looking_at(dir, upv), pos)
	else:
		light.position = Vector3(L["pos"][0], L["pos"][1], L["pos"][2])


func _make_floor(f: Dictionary) -> void:
	var m := MeshInstance3D.new()
	var plane := PlaneMesh.new()
	plane.size = Vector2(f["size"], f["size"]) * 2.0
	m.mesh = plane
	var mat := StandardMaterial3D.new()
	mat.albedo_color = _col(f["color"])
	mat.roughness = clampf(0.9 - float(f.get("reflect", 0.0)), 0.1, 1.0)
	mat.metallic_specular = 0.3
	m.material_override = mat
	m.position = Vector3(0, f["y"], 0)
	root.add_child(m)


func _process(_delta: float) -> bool:
	_frames += 1
	if _frames < WARM_FRAMES:
		return false
	var img := root.get_texture().get_image()
	if img == null or img.is_empty():
		_fail("goruntu alinamadi")
		return true
	if img.get_size() != _size:
		img.resize(_size.x, _size.y, Image.INTERPOLATE_LANCZOS)
	var e := img.save_png(_out)
	if e != OK:
		_fail("png yazilamadi (%d): %s" % [e, _out])
		return true
	print("YBT_OK: ", _out, " ", img.get_size())
	quit(0)
	return true
