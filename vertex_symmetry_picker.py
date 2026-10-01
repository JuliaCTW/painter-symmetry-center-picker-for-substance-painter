"""Vertex Symmetry Picker 1.0.0-beta1 -- read-only mesh analysis for Painter.

Install this single file in Painter's Python plugins directory.
No project writes, no symmetry writes, no visibility assumptions.
"""
import hashlib
import math
from pathlib import Path
import tempfile
from dataclasses import dataclass
from array import array

VERSION = '1.0.0-beta1'
_panel = None
_dock = None


def number(value):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError('请输入有限数值，不能使用 NaN 或 Infinity。')
    return value


@dataclass(frozen=True)
class Box:
    low: tuple
    high: tuple

    @property
    def center(self):
        return tuple(a * .5 + b * .5 for a, b in zip(self.low, self.high))

    @property
    def size(self):
        return tuple(b - a for a, b in zip(self.low, self.high))


def merge_boxes(boxes):
    boxes = list(boxes)
    if not boxes:
        raise ValueError('请至少勾选一个对象。')
    return Box(tuple(min(b.low[i] for b in boxes) for i in range(3)),
               tuple(max(b.high[i] for b in boxes) for i in range(3)))


@dataclass
class MeshSnapshot:
    groups: dict
    materials: dict
    face_counts: dict
    box: Box
    digest: str


def read_obj(path):
    """Group faces by OBJ object/group; bounds exclude unreferenced vertices.

    Memory is O(vertices + groups). Face index arrays are not retained.
    OBJ g lists are treated as a single membership signature, not duplicate meshes.
    """
    vertices = array('d')
    boxes, materials, counts = {}, {}, {}
    object_name, group_names, material = '', (), '(No material)'
    digest = hashlib.sha256()
    with Path(path).open(encoding='utf-8-sig') as stream:
        for line_no, line in enumerate(stream, 1):
            items = line.partition('#')[0].split()
            if not items:
                continue
            kind = items[0]
            try:
                if kind in ('v', 'o', 'g', 'f', 'usemtl'):
                    digest.update((' '.join(items) + '\n').encode('utf-8'))
                if kind == 'v':
                    if len(items) < 4:
                        raise ValueError('顶点坐标不足')
                    if len(items) == 5 and number(items[4]) != 1:
                        raise ValueError('暂不支持非 1 的齐次顶点权重')
                    vertices.extend(number(v) for v in items[1:4])
                elif kind == 'o':
                    object_name = ' '.join(items[1:])
                elif kind == 'g':
                    group_names = tuple(sorted(set(items[1:])))
                elif kind == 'usemtl':
                    material = ' '.join(items[1:]) or '(No material)'
                elif kind == 'f':
                    if len(items) < 4:
                        raise ValueError('面至少需要三个顶点')
                    key = (object_name, group_names)
                    if key not in boxes:
                        boxes[key] = ([math.inf] * 3, [-math.inf] * 3)
                        materials[key] = set()
                        counts[key] = 0
                    lo, hi = boxes[key]
                    for token in items[1:]:
                        raw = int(token.split('/')[0])
                        count = len(vertices) // 3
                        index = raw - 1 if raw > 0 else count + raw
                        if raw == 0 or not 0 <= index < count:
                            raise ValueError('顶点索引越界或前向引用')
                        for axis in range(3):
                            value = vertices[index * 3 + axis]
                            lo[axis] = min(lo[axis], value)
                            hi[axis] = max(hi[axis], value)
                    materials[key].add(material)
                    counts[key] += 1
            except (ValueError, OverflowError) as exc:
                raise ValueError('OBJ 第 %d 行：%s' % (line_no, exc)) from exc
    result = {key: Box(tuple(lo), tuple(hi)) for key, (lo, hi) in boxes.items()}
    if not result:
        raise ValueError('没有可读取的多边形对象。')
    return MeshSnapshot(result, materials, counts, merge_boxes(result.values()), digest.hexdigest())


def group_label(key):
    obj, groups = key
    group = ' + '.join(groups)
    if obj and group and obj != group:
        return obj + ' / ' + group
    return obj or group or '(Unnamed)'



def format_position(value):
    """Match Painter's six-decimal input; normalize rounded negative zero."""
    result = format(number(value), '.6f')
    return '0.000000' if result == '-0.000000' else result


def native_position(center, scene_center, radius):
    radius = number(radius)
    if radius <= 0:
        raise ValueError('Invalid scene radius / 场景尺寸无效')
    return tuple(number((number(p) - number(c)) / radius)
                 for p, c in zip(center, scene_center))


def validate_bounds(box, center, dimensions):
    scale = max(map(abs, dimensions))
    tolerance = max(scale * 1e-5, 1e-6)
    if any(abs(a-b) > tolerance for a,b in zip(box.center, center)) or any(
            abs(a-b) > tolerance for a,b in zip(box.size, dimensions)):
        raise ValueError('Exported mesh and scene bounds differ. Conversion stopped. / 导出网格与场景范围不一致，已停止换算。')


def load_geometry(path):
    snapshot = read_obj(path)
    vertices, faces = [], []
    obj, groups = '', ()
    with Path(path).open(encoding='utf-8-sig') as stream:
        for line in stream:
            parts = line.partition('#')[0].split()
            if not parts:
                continue
            if parts[0] == 'v':
                vertices.append(tuple(number(x) for x in parts[1:4]))
            elif parts[0] == 'o':
                obj = ' '.join(parts[1:])
            elif parts[0] == 'g':
                groups = tuple(sorted(set(parts[1:])))
            elif parts[0] == 'f':
                indices = [int(p.split('/')[0]) for p in parts[1:]]
                indices = tuple(i-1 if i > 0 else len(vertices)+i for i in indices)
                faces.append(((obj, groups), indices))
    return snapshot, vertices, faces


# Horizontal, vertical, toward-viewer axes. Orthographic views avoid camera ambiguity.
VIEWS = ((0,1,2,1), (0,1,2,-1), (2,1,0,1), (2,1,0,-1), (0,2,1,1), (0,2,1,-1))


def project_vertices(vertices, view):
    h, v, d, sign = VIEWS[view]
    return [(p[h]*sign, p[v], p[d]*sign) for p in vertices]


def face_depth_at(x, y, polygon):
    """Even/odd containment plus plane interpolation for planar OBJ faces.

    No triangle fan: concave polygons must not falsely occlude a vertex.
    Nonplanar faces are an approximation; Painter exports triangulated geometry.
    """
    inside = False
    previous = polygon[-1]
    for current in polygon:
        ax, ay = previous[:2]
        bx, by = current[:2]
        if (ay > y) != (by > y) and x < (bx-ax)*(y-ay)/(by-ay)+ax:
            inside = not inside
        previous = current
    if not inside:
        return None
    a = polygon[0]
    for i in range(1, len(polygon)-1):
        b, c = polygon[i], polygon[i+1]
        determinant = (b[0]-a[0])*(c[1]-a[1])-(c[0]-a[0])*(b[1]-a[1])
        if abs(determinant) > 1e-15:
            u = ((x-a[0])*(c[1]-a[1])-(c[0]-a[0])*(y-a[1]))/determinant
            v = ((b[0]-a[0])*(y-a[1])-(x-a[0])*(b[1]-a[1]))/determinant
            return a[2] + u*(b[2]-a[2]) + v*(c[2]-a[2])
    return None


def pick_vertex(projected, ids, polygons, x, y, radius, depth_tolerance):
    candidates = sorted(((projected[i][0]-x)**2+(projected[i][1]-y)**2,
                         -projected[i][2], i) for i in ids
                        if (projected[i][0]-x)**2+(projected[i][1]-y)**2 <= radius*radius)
    for _, _, index in candidates:
        px, py, depth = projected[index]
        if all((surface := face_depth_at(px, py, poly)) is None or
               surface <= depth + depth_tolerance for poly in polygons):
            return index
    return None


def make_panel():
    from PySide6 import QtCore as C, QtGui as G, QtWidgets as W
    import substance_painter.project as project
    import substance_painter.export as export

    class Preview(W.QWidget):
        picked = C.Signal(int)

        def __init__(self):
            super().__init__()
            self.setMinimumSize(360, 300)
            self.vertices, self.ids, self.faces = [], [], []
            self.projected, self.polygons = [], []
            self.view = 0
            self.selected = None
            self.scale, self.cx, self.cy = 1., 0., 0.
            self.pan = C.QPointF()
            self.drag_start = None
            self.language = 0

        def set_geometry(self, vertices, faces):
            self.vertices, self.faces = vertices, faces
            self.ids = sorted({i for face in faces for i in face})
            self.selected = None
            self.reproject()

        def reproject(self):
            self.projected = project_vertices(self.vertices, self.view)
            self.polygons = [[self.projected[i] for i in f] for f in self.faces]
            self.draw_order = sorted(range(len(self.faces)), key=lambda i: sum(p[2] for p in self.polygons[i])/len(self.polygons[i]))
            self.fit()

        def fit(self):
            if self.ids:
                xs = [self.projected[i][0] for i in self.ids]
                ys = [self.projected[i][1] for i in self.ids]
                self.cx, self.cy = (min(xs)+max(xs))/2, (min(ys)+max(ys))/2
                self.span = max(max(xs)-min(xs), max(ys)-min(ys), 1e-9)
                self.scale = min((self.width()-40)/max(max(xs)-min(xs),self.span*.01),
                                 (self.height()-40)/max(max(ys)-min(ys),self.span*.01))
            self.pan = C.QPointF()
            self.update()

        def screen(self, p):
            return C.QPointF(self.width()/2+self.pan.x()+(p[0]-self.cx)*self.scale,
                             self.height()/2+self.pan.y()-(p[1]-self.cy)*self.scale)

        def paintEvent(self, event):
            painter = G.QPainter(self)
            painter.fillRect(self.rect(), G.QColor('#20262d'))
            painter.setRenderHint(G.QPainter.RenderHint.Antialiasing)
            painter.setPen(G.QPen(G.QColor('#8196a5'), .65))
            painter.setBrush(G.QColor('#435969'))
            for i in getattr(self, 'draw_order', []):
                painter.drawPolygon(G.QPolygonF([self.screen(p) for p in self.polygons[i]]))
            if self.selected is not None:
                p = self.screen(self.projected[self.selected])
                painter.setBrush(G.QColor('#ffd45a'))
                painter.setPen(G.QPen(G.QColor('#1a1a1a'), 2))
                painter.drawEllipse(p, 6, 6)
                painter.setPen(G.QColor('#ffd45a'))
                painter.drawText(p+C.QPointF(10,-10), '#'+str(self.selected+1))
            painter.setPen(G.QColor('#eeeeee'))
            painter.drawText(12, 22, ('左键选点 · 滚轮缩放 · 右键拖动平移', 'Click vertex · Wheel zoom · Right-drag pan')[self.language])
            painter.end()

        def wheelEvent(self, event):
            before = event.position()-C.QPointF(self.width()/2,self.height()/2)-self.pan
            factor = 1.2**(event.angleDelta().y()/120)
            new_scale = max(1e-10, min(1e12, self.scale*factor))
            factor = new_scale/self.scale
            self.pan += before*(1-factor)
            self.scale = new_scale
            self.update()
            event.accept()

        def mousePressEvent(self, event):
            if event.button() == C.Qt.MouseButton.RightButton:
                self.drag_start = event.position()
            elif event.button() == C.Qt.MouseButton.LeftButton and self.ids:
                pos = event.position()-C.QPointF(self.width()/2,self.height()/2)-self.pan
                index = pick_vertex(self.projected, self.ids, self.polygons,
                                    pos.x()/self.scale+self.cx, -pos.y()/self.scale+self.cy,
                                    10/self.scale, self.span*1e-7)
                self.selected = index
                self.picked.emit(-1 if index is None else index)
                self.update()

        def mouseMoveEvent(self, event):
            if self.drag_start is not None:
                self.pan += event.position()-self.drag_start
                self.drag_start = event.position()
                self.update()

        def mouseReleaseEvent(self, event):
            self.drag_start = None

    class Panel(W.QWidget):
        def __init__(self):
            super().__init__()
            self.setObjectName('VertexSymmetryPickerIndependent')
            self.setWindowTitle('Vertex Symmetry Picker 1.0 Beta')
            self.signature = None
            self.vertices, self.faces = [], []
            self.result = None
            self.settings = C.QSettings('VertexSymmetryPicker', 'Preferences')
            layout = W.QVBoxLayout(self)
            self.language = W.QComboBox()
            self.language.addItems(['中文', 'English'])
            self.language.setCurrentIndex(1 if str(self.settings.value('language',0)) == '1' else 0)
            layout.addWidget(self.language)
            self.note = W.QLabel()
            self.note.setWordWrap(True)
            layout.addWidget(self.note)
            self.read_button = W.QPushButton()
            self.read_button.clicked.connect(self.read_project)
            layout.addWidget(self.read_button)
            self.list = W.QListWidget()
            self.list.setMaximumHeight(100)
            self.list.itemChanged.connect(self.show_selected)
            layout.addWidget(self.list)
            row = W.QHBoxLayout()
            self.view = W.QComboBox()
            self.view.currentIndexChanged.connect(self.change_view)
            row.addWidget(self.view)
            self.fit_button = W.QPushButton()
            self.fit_button.clicked.connect(lambda: self.preview.fit())
            row.addWidget(self.fit_button)
            layout.addLayout(row)
            self.preview = Preview()
            self.preview.picked.connect(self.select_vertex)
            layout.addWidget(self.preview, 1)
            self.point = W.QLabel('—')
            self.point.setWordWrap(True)
            layout.addWidget(self.point)
            self.group = W.QGroupBox()
            grid = W.QGridLayout(self.group)
            self.fields, self.buttons = [], []
            for axis,name in enumerate('XYZ'):
                grid.addWidget(W.QLabel(name),axis,0)
                field = W.QLineEdit('—')
                field.setReadOnly(True)
                button = W.QPushButton()
                button.clicked.connect(lambda checked=False,a=axis: self.copy(a))
                button.setEnabled(False)
                grid.addWidget(field,axis,1)
                grid.addWidget(button,axis,2)
                self.fields.append(field)
                self.buttons.append(button)
            layout.addWidget(self.group)
            self.status = W.QLabel()
            self.status.setWordWrap(True)
            layout.addWidget(self.status)
            self.message = ('读取模型后勾选身体，再点击预览中的顶点。', 'Read objects, check the body, then click a vertex in the preview.')
            self.language.currentIndexChanged.connect(self.translate)
            self.translate()
            self.timer = C.QTimer(self)
            self.timer.setInterval(1500)
            self.timer.timeout.connect(self.check_project)
            self.timer.start()

        def text(self, zh,en):
            return (zh,en)[self.language.currentIndex()]

        def say(self,zh,en):
            self.message = (zh,en)
            self.status.setText(self.text(zh,en))

        def translate(self,*_):
            self.settings.setValue('language',self.language.currentIndex())
            self.preview.language = self.language.currentIndex()
            self.preview.update()
            self.note.setText(self.text('独立预览选点测试版：不在 Painter 主视口选点。\n只勾选需要的对象；X/Y 换算沿用旧工具，Z 尚未实测。', 'Beta: pick vertices in this independent preview, not the Painter viewport.\nCheck only needed objects. Uses the previous X/Y conversion; Z remains unverified.'))
            self.read_button.setText(self.text('1  读取当前项目对象', '1  Read current project objects'))
            self.fit_button.setText(self.text('适应窗口', 'Fit view'))
            index = max(0,self.view.currentIndex())
            self.view.blockSignals(True)
            self.view.clear()
            self.view.addItems(['正面 +Z','背面 −Z','侧面 +X','侧面 −X','俯视 +Y','仰视 −Y'] if self.language.currentIndex()==0 else ['Front +Z','Back −Z','Side +X','Side −X','Top +Y','Bottom −Y'])
            self.view.setCurrentIndex(index)
            self.view.blockSignals(False)
            self.group.setTitle(self.text('3  复制到同一轴的 Axis position', '3  Copy to the matching Axis position'))
            for button in self.buttons:
                button.setText(self.text('复制', 'Copy'))
            self.status.setText(self.text(*self.message))

        def invalidate(self):
            self.result = None
            self.preview.selected = None
            self.preview.update()
            self.point.setText('—')
            for field,button in zip(self.fields,self.buttons):
                field.setText('—')
                button.setEnabled(False)

        def project_signature(self):
            if not project.is_open():
                return None
            b = project.get_scene_bounding_box()
            return (str(project.get_uuid()),str(project.last_imported_mesh_path()),tuple(b.center),tuple(b.dimensions),number(b.radius))

        def clear(self):
            self.signature = None
            self.vertices,self.faces = [],[]
            self.list.blockSignals(True)
            self.list.clear()
            self.list.blockSignals(False)
            self.preview.set_geometry([],[])
            self.invalidate()

        def check_project(self):
            if self.signature is not None:
                try:
                    if project.is_busy() or self.signature != self.project_signature():
                        self.clear()
                        self.say('项目已改变，请重新读取。','Project changed. Read objects again.')
                except Exception:
                    self.clear()
                    self.say('无法验证项目，请重新读取。','Cannot verify project. Read objects again.')

        def read_project(self):
            self.clear()
            self.read_button.setEnabled(False)
            try:
                if not project.is_open() or project.is_busy() or not project.is_in_edition_state():
                    raise ValueError(self.text('请打开项目并等待处理完成。','Open a project and wait for ongoing operations.'))
                signature = self.project_signature()
                with tempfile.TemporaryDirectory(prefix='vertex_picker_') as temporary:
                    folder = Path(temporary).resolve()
                    if folder.parent != Path(tempfile.gettempdir()).resolve() or not folder.name.startswith('vertex_picker_'):
                        raise ValueError('Invalid temporary folder / 临时目录无效')
                    destination = folder/'snapshot.obj'
                    response = export.export_mesh(str(destination),export.MeshExportOption.BaseMesh)
                    if response.status != export.ExportStatus.Success:
                        raise RuntimeError(response.message)
                    snapshot,vertices,faces = load_geometry(destination)
                validate_bounds(snapshot.box,signature[2],signature[3])
                native_position(snapshot.box.center,signature[2],signature[4])
                if signature != self.project_signature():
                    raise ValueError('Project changed / 项目改变')
                self.vertices,self.faces,self.signature = vertices,faces,signature
                self.list.blockSignals(True)
                for key in sorted(snapshot.groups):
                    item = W.QListWidgetItem(group_label(key))
                    item.setData(C.Qt.ItemDataRole.UserRole,key)
                    item.setFlags(item.flags()|C.Qt.ItemFlag.ItemIsUserCheckable)
                    item.setCheckState(C.Qt.CheckState.Unchecked)
                    self.list.addItem(item)
                self.say('2  勾选对象，然后在预览中点击中线顶点。','2  Check objects, then click a centerline vertex in the preview.')
            except Exception as exc:
                self.clear()
                self.say('读取失败：'+str(exc),'Read failed: '+str(exc))
            finally:
                self.list.blockSignals(False)
                self.read_button.setEnabled(True)

        def show_selected(self,*_):
            self.invalidate()
            self.check_project()
            keys = set()
            for i in range(self.list.count()):
                item = self.list.item(i)
                if item.checkState()==C.Qt.CheckState.Checked:
                    obj,groups = item.data(C.Qt.ItemDataRole.UserRole)
                    keys.add((obj,tuple(groups)))
            faces = [face for key,face in self.faces if key in keys]
            if sum(len(f) for f in faces)>180000:
                self.preview.set_geometry([],[])
                self.say('所选网格过密，请减少勾选对象。本测试版最多预览 180000 个面角点。','Selection too dense. Select fewer objects; preview limit is 180,000 face corners.')
                return
            self.preview.set_geometry(self.vertices,faces)
            self.say('点击可见顶点；黄色圆点表示选中位置。','Click a visible vertex; the yellow marker shows the selection.')

        def change_view(self,index):
            if index>=0 and hasattr(self,'preview'):
                self.invalidate()
                self.preview.view = index
                self.preview.reproject()

        def select_vertex(self,index):
            self.check_project()
            if self.signature is None or index<0:
                self.invalidate()
                self.say('未选中顶点，请放大后点击网格交点。','No vertex selected. Zoom in and click a wire intersection.')
                return
            point = self.vertices[index]
            self.result = native_position(point,self.signature[2],self.signature[4])
            self.point.setText('Vertex #%d | X %.9g   Y %.9g   Z %.9g' % (index+1,*point))
            for value,field,button in zip(self.result,self.fields,self.buttons):
                field.setText(format_position(value))
                button.setEnabled(True)
            self.say('已选中真实顶点。左右对称请复制 X。','Real vertex selected. Copy X for left/right symmetry.')

        def copy(self,axis):
            self.check_project()
            if self.result is not None:
                value = format_position(self.result[axis])
                W.QApplication.clipboard().setText(value)
                self.say('已复制 %s：%s' % ('XYZ'[axis],value),'Copied %s: %s' % ('XYZ'[axis],value))

        def shutdown(self):
            self.timer.stop()

    return Panel()


def start_plugin():
    global _panel,_dock
    import substance_painter.ui as ui
    if _dock is not None:
        _dock.show()
        return
    _panel = make_panel()
    try:
        _dock = ui.add_dock_widget(_panel)
        _panel.show()
        _dock.show()
    except Exception:
        _panel.shutdown()
        _panel.deleteLater()
        _panel = _dock = None
        raise


def close_plugin():
    global _panel,_dock
    if _panel is not None:
        _panel.shutdown()
    if _dock is not None:
        import substance_painter.ui as ui
        ui.delete_ui_element(_dock)
    _panel = _dock = None


