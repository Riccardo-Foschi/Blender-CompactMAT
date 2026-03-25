bl_info = {
    "name": "EasyMAT",
    "author": "Riccardo Foschi + Gemini 3.1",
    "version": (1, 17),
    "blender": (3, 0, 0),
    "location": "Shader Editor > Sidebar > EasyMAT",
    "description": "Helps create and assign materials in an easy way without needing to have a mesh selected or to link materials",
    "warning": "",
    "doc_url": "",
    "category": "Material",
}

import bpy
import os
from bpy_extras.io_utils import ImportHelper
from bpy_extras import view3d_utils

# -------------------------------------------------------------------
# Helper Functions
# -------------------------------------------------------------------

def poll_material(self, mat):
    """Hide 'Dots Stroke' and other grease pencil materials from the dropdown."""
    is_gp = getattr(mat, "is_grease_pencil", False) or getattr(mat, "grease_pencil", None) is not None
    return mat.name != "Dots Stroke" and not is_gp

def get_bsdf(mat):
    if not mat or not mat.use_nodes: return None
    return next((n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)

def get_socket(mat, possible_names):
    bsdf = get_bsdf(mat)
    if not bsdf: return None
    for name in possible_names:
        if name in bsdf.inputs:
            return bsdf.inputs[name]
    return None

def get_linked_tex_node(socket, context_type="COLOR"):
    if not socket or not socket.is_linked: return None
    node = socket.links[0].from_node
    
    if context_type == "NORMAL":
        if node.type in {'NORMAL_MAP', 'BUMP'}:
            img_sock = node.inputs.get('Color') if node.type == 'NORMAL_MAP' else node.inputs.get('Height')
            if img_sock and img_sock.is_linked:
                img_node = img_sock.links[0].from_node
                if img_node.type == 'TEX_IMAGE': return img_node
    elif context_type == "DISP":
        if node.type == 'DISPLACEMENT' and node.inputs['Height'].is_linked:
            img_node = node.inputs['Height'].links[0].from_node
            if img_node.type == 'TEX_IMAGE': return img_node
    elif node.type == 'TEX_IMAGE':
        return node
    return None

def get_or_create_mapping(mat):
    tree = mat.node_tree
    mapping = tree.nodes.get("CompactMAT_Mapping")
    tex_coord = tree.nodes.get("CompactMAT_TexCoord")
    
    if not mapping:
        mapping = tree.nodes.new('ShaderNodeMapping')
        mapping.name = "CompactMAT_Mapping"
    if not tex_coord:
        tex_coord = tree.nodes.new('ShaderNodeTexCoord')
        tex_coord.name = "CompactMAT_TexCoord"
    if not mapping.inputs['Vector'].is_linked:
        tree.links.new(tex_coord.outputs['UV'], mapping.inputs['Vector'])
        
    return mapping

def align_nodes(mat):
    """Automatically organizes nodes to close gaps, align columns, and place intermediaries under the BSDF."""
    bsdf = get_bsdf(mat)
    if not bsdf: return
    
    bsdf_x, bsdf_y = bsdf.location.x, bsdf.location.y
    tex_x = bsdf_x - 350  # Fixed column for textures
    
    output = next((n for n in mat.node_tree.nodes if n.type == 'OUTPUT_MATERIAL'), None)
    if output:
        output.location = (bsdf_x + 350, bsdf_y)
    
    # Position Intermediary Nodes neatly UNDER the Principled BSDF
    norm_socket = get_socket(mat, ["Normal"])
    if norm_socket and norm_socket.is_linked:
        norm_node = norm_socket.links[0].from_node
        if norm_node.type in {'NORMAL_MAP', 'BUMP'}:
            norm_node.location = (bsdf_x, bsdf_y - 650)
            
    if output:
        disp_socket = output.inputs.get("Displacement")
        if disp_socket and disp_socket.is_linked:
            disp_node = disp_socket.links[0].from_node
            if disp_node.type == 'DISPLACEMENT':
                disp_node.location = (bsdf_x, bsdf_y - 850)

    # Re-stack textures dynamically to close any gaps
    ordered_sockets = ["Base Color", "Metallic", "Roughness", "Alpha", "Normal", "Displacement"]
    current_y = bsdf_y
    
    for s_name in ordered_sockets:
        sock = None
        if s_name == "Displacement" and output:
            sock = output.inputs.get("Displacement")
        elif s_name == "Metallic":
            sock = get_socket(mat, ["Metallic Weight", "Metallic"])
        else:
            sock = get_socket(mat, [s_name])
            
        if not sock or not sock.is_linked: continue
        
        tex_node = None
        first_node = sock.links[0].from_node
        
        # Traverse backward to find the actual image texture
        if s_name == "Normal":
            if first_node.type in {'NORMAL_MAP', 'BUMP'}:
                inner_sock = first_node.inputs.get('Color') if first_node.type == 'NORMAL_MAP' else first_node.inputs.get('Height')
                if inner_sock and inner_sock.is_linked:
                    tex_node = inner_sock.links[0].from_node
        elif s_name == "Displacement":
            if first_node.type == 'DISPLACEMENT':
                inner_sock = first_node.inputs.get('Height')
                if inner_sock and inner_sock.is_linked:
                    tex_node = inner_sock.links[0].from_node
        else:
            if first_node.type == 'TEX_IMAGE':
                tex_node = first_node
                
        # If an image texture exists for this slot, align it and drop the Y position
        if tex_node and tex_node.type == 'TEX_IMAGE':
            tex_node.location = (tex_x, current_y)
            current_y -= 300
            
    # Position mapping nodes neatly to the left
    mapping = mat.node_tree.nodes.get("CompactMAT_Mapping")
    tex_coord = mat.node_tree.nodes.get("CompactMAT_TexCoord")
    
    if mapping: mapping.location = (tex_x - 220, bsdf_y)
    if tex_coord: tex_coord.location = (tex_x - 420, bsdf_y)

def connect_texture(mat, img, target_socket_name, context_type="COLOR", normal_mode='NORMAL'):
    tree = mat.node_tree
    
    if context_type == "DISP":
        output = next((n for n in tree.nodes if n.type == 'OUTPUT_MATERIAL'), None)
        socket = output.inputs.get("Displacement") if output else None
    else:
        names = [target_socket_name]
        if target_socket_name == "Metallic": names = ["Metallic Weight", "Metallic"]
        socket = get_socket(mat, names)
        
    if not socket: return False
    
    mapping = get_or_create_mapping(mat)
    bpy.ops.easymat.remove_texture(socket_name=target_socket_name, context_type=context_type)

    tex_node = tree.nodes.new('ShaderNodeTexImage')
    tex_node.image = img
    tree.links.new(mapping.outputs['Vector'], tex_node.inputs['Vector'])

    if context_type == "NORMAL":
        if normal_mode == 'NORMAL':
            node = tree.nodes.new('ShaderNodeNormalMap')
            tree.links.new(tex_node.outputs['Color'], node.inputs['Color'])
        else:
            node = tree.nodes.new('ShaderNodeBump')
            tree.links.new(tex_node.outputs['Color'], node.inputs['Height'])
        tree.links.new(node.outputs['Normal'], socket)
    elif context_type == "DISP":
        disp_node = tree.nodes.new('ShaderNodeDisplacement')
        disp_node.inputs['Scale'].default_value = 0.1
        tree.links.new(tex_node.outputs['Color'], disp_node.inputs['Height'])
        tree.links.new(disp_node.outputs['Displacement'], socket)
    else:
        tree.links.new(tex_node.outputs['Color'], socket)
        
    align_nodes(mat) # Auto-organize layout
    return True

def get_object_under_mouse(context, event):
    area = next((a for a in context.screen.areas if a.type == 'VIEW_3D'), None)
    if not area: return None
    
    region = next((r for r in area.regions if r.type == 'WINDOW'), None)
    if not region: return None
    
    space = area.spaces.active
    region_data = space.region_3d
    
    coord = (event.mouse_x - region.x, event.mouse_y - region.y)
    view_vector = view3d_utils.region_2d_to_vector_3d(region, region_data, coord)
    ray_origin = view3d_utils.region_2d_to_origin_3d(region, region_data, coord)
    
    depsgraph = context.evaluated_depsgraph_get()
    result, location, normal, index, object, matrix = context.scene.ray_cast(depsgraph, ray_origin, view_vector)
    
    return object if result else None

# -------------------------------------------------------------------
# Operators
# -------------------------------------------------------------------

class COMPACTMAT_OT_pick_material(bpy.types.Operator):
    """Pick a material from a mesh in the 3D viewport"""
    bl_idname = "easymat.pick_material"
    bl_label = "Pick Material"
    bl_options = {'UNDO'}

    def invoke(self, context, event):
        context.window_manager.modal_handler_add(self)
        context.workspace.status_text_set("Hover over a 3D Viewport and click on an object to pick its material. ESC/Right Click to cancel.")
        context.window.cursor_modal_set('EYEDROPPER')
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type in {'ESC', 'RIGHTMOUSE'}:
            context.workspace.status_text_set(None)
            context.window.cursor_modal_restore()
            return {'CANCELLED'}

        elif event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            
            clicked_obj = get_object_under_mouse(context, event)

            if clicked_obj and clicked_obj.type == 'MESH' and clicked_obj.active_material:
                context.scene.compactmat_active_material = clicked_obj.active_material
                self.report({'INFO'}, f"Picked material: {clicked_obj.active_material.name}")
            else:
                self.report({'WARNING'}, "No material found on clicked location.")

            context.workspace.status_text_set(None)
            context.window.cursor_modal_restore()
            
            for window in context.window_manager.windows:
                for area in window.screen.areas: area.tag_redraw()
                
            return {'FINISHED'}

        return {'PASS_THROUGH'}

class COMPACTMAT_OT_new_material(bpy.types.Operator):
    bl_idname = "easymat.new_material"
    bl_label = "New Material"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        mat = bpy.data.materials.new(name="New Material")
        mat.use_nodes = True
        mat.use_fake_user = True 
        context.scene.compactmat_active_material = mat
        
        for window in context.window_manager.windows:
            for area in window.screen.areas: area.tag_redraw()
        return {'FINISHED'}

class COMPACTMAT_OT_remove_material(bpy.types.Operator):
    """Clear all materials from selected meshes"""
    bl_idname = "easymat.remove_material"
    bl_label = "Unassign Materials"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return len(context.selected_objects) > 0

    def execute(self, context):
        count = 0
        for obj in context.selected_objects:
            if obj.type == 'MESH':
                if len(obj.data.materials) > 0:
                    obj.data.materials.clear()
                    count += 1
        
        if count > 0:
            self.report({'INFO'}, f"Cleared materials from {count} meshes.")
        else:
            self.report({'INFO'}, "No materials to clear on selected meshes.")
            
        for window in context.window_manager.windows:
            for area in window.screen.areas: area.tag_redraw()
            
        return {'FINISHED'}

class COMPACTMAT_OT_assign_material(bpy.types.Operator):
    bl_idname = "easymat.assign_material"
    bl_label = "Assign"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.scene.compactmat_active_material is not None and len(context.selected_objects) > 0

    def invoke(self, context, event):
        has_multiple_slots = any(len(obj.material_slots) > 1 for obj in context.selected_objects if obj.type == 'MESH')
        if has_multiple_slots:
            return context.window_manager.invoke_props_dialog(self)
        return self.execute(context)

    def draw(self, context):
        self.layout.label(text="Objects have multiple materials. All will be replaced.", icon='ERROR')

    def execute(self, context):
        mat = context.scene.compactmat_active_material
        for obj in context.selected_objects:
            if obj.type == 'MESH':
                obj.data.materials.clear()
                obj.data.materials.append(mat)
        return {'FINISHED'}

class COMPACTMAT_OT_setup_displacement(bpy.types.Operator):
    """Setup a Subdivision modifier and enable Displacement for selected objects"""
    bl_idname = "easymat.setup_displacement"
    bl_label = "Setup Displacement Subdivisions"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.scene.compactmat_active_material is not None

    def execute(self, context):
        mat = context.scene.compactmat_active_material
        ui = context.scene.compactmat_ui
        
        if not context.selected_objects:
            self.report({'ERROR'}, "No mesh selected! Please select an object first.")
            return {'CANCELLED'}
            
        if hasattr(mat, "displacement_method"):
            mat.displacement_method = 'BOTH'
        elif hasattr(mat.cycles, "displacement_method"):
            mat.cycles.displacement_method = 'BOTH'
            
        applied = False
        for obj in context.selected_objects:
            if obj.type == 'MESH':
                applied = True
                mod = next((m for m in obj.modifiers if m.type == 'SUBSURF'), None)
                if not mod:
                    mod = obj.modifiers.new(name="Subdivision", type='SUBSURF')
                
                mod.subdivision_type = 'SIMPLE'
                mod.levels = ui.disp_subdiv_levels
                mod.render_levels = ui.disp_subdiv_levels
                
        if not applied:
            self.report({'ERROR'}, "Selected object is not a mesh!")
            return {'CANCELLED'}
            
        self.report({'INFO'}, f"Applied Subdivisions (Level {ui.disp_subdiv_levels}) and set to Displacement and Bump.")
        return {'FINISHED'}

class COMPACTMAT_OT_upload_all_textures(bpy.types.Operator, ImportHelper):
    bl_idname = "easymat.upload_all_textures"
    bl_label = "Upload All Textures"
    bl_options = {'UNDO'}
    
    directory: bpy.props.StringProperty(subtype='DIR_PATH')
    files: bpy.props.CollectionProperty(type=bpy.types.OperatorFileListElement)
    filter_glob: bpy.props.StringProperty(default="*.jpg;*.jpeg;*.png;*.tif;*.tiff;*.exr", options={'HIDDEN'})

    def execute(self, context):
        mat = context.scene.compactmat_active_material
        if not mat or not mat.use_nodes: return {'CANCELLED'}
        
        for file_elem in self.files:
            filepath = os.path.join(self.directory, file_elem.name)
            name = file_elem.name.lower()
            
            target, c_type = None, "COLOR"
            
            if any(x in name for x in ['color', 'albedo', 'diff', 'col', 'base']): target = "Base Color"
            elif any(x in name for x in ['rough', 'rgh']): target = "Roughness"
            elif any(x in name for x in ['metal', 'met', 'mtl']): target = "Metallic"
            elif any(x in name for x in ['alpha', 'opacity', 'transparency', 'mask']): target = "Alpha"
            elif any(x in name for x in ['norm', 'nrm', 'nd']): target = "Normal"; c_type = "NORMAL"
            elif any(x in name for x in ['disp', 'height']): target = "Displacement"; c_type = "DISP"
            
            if target:
                img = bpy.data.images.load(filepath)
                if target != "Base Color":
                    try: img.colorspace_settings.name = 'Non-Color'
                    except: pass
                connect_texture(mat, img, target, c_type, context.scene.compactmat_normal_mode)
                
                if target == "Alpha" and context.scene.render.engine != 'CYCLES':
                    if hasattr(mat, "blend_method"): mat.blend_method = 'HASHED'
                    if hasattr(mat, "shadow_method"): mat.shadow_method = 'HASHED'
                
        return {'FINISHED'}

class COMPACTMAT_OT_add_texture(bpy.types.Operator, ImportHelper):
    bl_idname = "easymat.add_texture"
    bl_label = "Select Texture"
    bl_options = {'UNDO'}
    
    filter_glob: bpy.props.StringProperty(default="*.jpg;*.jpeg;*.png;*.tif;*.tiff;*.exr", options={'HIDDEN'})
    socket_name: bpy.props.StringProperty()
    context_type: bpy.props.StringProperty(default="COLOR") 

    def execute(self, context):
        mat = context.scene.compactmat_active_material
        img = bpy.data.images.load(self.filepath)
        
        non_color_triggers = ["Metallic", "Roughness", "Alpha", "Normal", "Bump", "Displacement", "Weight", "IOR", "Scale"]
        if any(trig in self.socket_name for trig in non_color_triggers) or self.context_type in ["NORMAL", "DISP"]:
            try: img.colorspace_settings.name = 'Non-Color'
            except: pass

        connect_texture(mat, img, self.socket_name, self.context_type, context.scene.compactmat_normal_mode)
        
        if self.socket_name == "Alpha" and context.scene.render.engine != 'CYCLES':
            if hasattr(mat, "blend_method"): mat.blend_method = 'HASHED'
            if hasattr(mat, "shadow_method"): mat.shadow_method = 'HASHED'
            
        return {'FINISHED'}

class COMPACTMAT_OT_remove_texture(bpy.types.Operator):
    bl_idname = "easymat.remove_texture"
    bl_label = "Remove Texture"
    bl_options = {'UNDO'}
    
    socket_name: bpy.props.StringProperty()
    context_type: bpy.props.StringProperty(default="COLOR")

    def execute(self, context):
        mat = context.scene.compactmat_active_material
        tree = mat.node_tree
        
        socket = get_socket(mat, [self.socket_name]) if self.context_type != "DISP" else \
                 next((n for n in tree.nodes if n.type == 'OUTPUT_MATERIAL'), None).inputs.get("Displacement")
                 
        if not socket or not socket.is_linked: return {'CANCELLED'}
        
        linked_node = socket.links[0].from_node
        nodes_to_remove = []

        if self.context_type == "NORMAL" and linked_node.type in {'NORMAL_MAP', 'BUMP'}:
            nodes_to_remove.append(linked_node)
            img_sock = linked_node.inputs.get('Color') if linked_node.type == 'NORMAL_MAP' else linked_node.inputs.get('Height')
            if img_sock and img_sock.is_linked:
                nodes_to_remove.append(img_sock.links[0].from_node)
        elif self.context_type == "DISP" and linked_node.type == 'DISPLACEMENT':
            nodes_to_remove.append(linked_node)
            if linked_node.inputs['Height'].is_linked:
                nodes_to_remove.append(linked_node.inputs['Height'].links[0].from_node)
        elif linked_node.type == 'TEX_IMAGE':
            nodes_to_remove.append(linked_node)

        for n in nodes_to_remove: tree.nodes.remove(n)
        
        align_nodes(mat) # Automatically close the gap!
        return {'FINISHED'}

# -------------------------------------------------------------------
# Properties and UI
# -------------------------------------------------------------------

class CompactmatUIProps(bpy.types.PropertyGroup):
    show_base_color: bpy.props.BoolProperty(default=False)
    show_metallic: bpy.props.BoolProperty(default=False)
    show_roughness: bpy.props.BoolProperty(default=False)
    show_alpha: bpy.props.BoolProperty(default=False)
    show_normal: bpy.props.BoolProperty(default=False)
    show_displacement: bpy.props.BoolProperty(default=False)
    show_subsurface: bpy.props.BoolProperty(default=False)
    show_transmission: bpy.props.BoolProperty(default=False)
    show_coat: bpy.props.BoolProperty(default=False)
    show_sheen: bpy.props.BoolProperty(default=False)
    show_emission: bpy.props.BoolProperty(default=False)
    show_thin_film: bpy.props.BoolProperty(default=False)
    
    disp_subdiv_levels: bpy.props.IntProperty(
        name="Levels",
        description="Number of subdivisions to apply",
        default=5,
        min=1,
        max=10
    )

def draw_socket_row(layout, socket, context_type="COLOR", show_prop=True, custom_name=None):
    if not socket: return
    row = layout.row(align=True)
    
    if show_prop and not socket.is_linked:
        row.prop(socket, "default_value", text=custom_name or socket.name)
    elif not show_prop and not socket.is_linked:
        row.label(text=custom_name or socket.name)
        
    if not socket.is_linked:
        op = row.operator("easymat.add_texture", text="", icon='FILE_FOLDER')
        op.socket_name = socket.name
        op.context_type = context_type
    else:
        tex_node = get_linked_tex_node(socket, context_type)
        if tex_node and tex_node.image:
            row.label(text=tex_node.image.name, icon='IMAGE_DATA')
            
            op_add = row.operator("easymat.add_texture", text="", icon='FILE_FOLDER')
            op_add.socket_name = socket.name
            op_add.context_type = context_type
            
            op_rm = row.operator("easymat.remove_texture", text="", icon='X')
            op_rm.socket_name = socket.name
            op_rm.context_type = context_type
        else:
            row.label(text="Custom Node Tree", icon='NODETREE')

class COMPACTMAT_PT_main_panel(bpy.types.Panel):
    bl_label = "EasyMAT"
    bl_idname = "COMPACTMAT_PT_main_panel"
    bl_space_type = 'NODE_EDITOR'
    bl_region_type = 'UI'
    bl_category = 'EasyMAT'

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        ui = scene.compactmat_ui
        mat = scene.compactmat_active_material

        row = layout.row(align=True)
        row.prop(scene, "compactmat_active_material", text="Material")
        row.operator("easymat.pick_material", text="", icon='EYEDROPPER')

        if mat:
            layout.prop(mat, "name", text="Name")

        row = layout.row()
        row.operator("easymat.new_material", text="New", icon='ADD', depress=True)
        
        row = layout.row(align=True)
        row.operator("easymat.assign_material", text="Assign", icon='MATERIAL', depress=True)
        row.operator("easymat.remove_material", text="Unassign", icon='TRASH', depress=True)

        if not mat: return

        layout.separator()
        layout.template_preview(mat)
        layout.separator()
        
        layout.operator("easymat.upload_all_textures", text="Upload All Textures", icon='NODETREE')
        layout.separator()

        if not mat.use_nodes: return
        bsdf = get_bsdf(mat)
        if not bsdf: return

        def draw_box(prop_name, label, draw_func):
            box = layout.box()
            row = box.row()
            is_open = getattr(ui, prop_name)
            row.prop(ui, prop_name, icon='TRIA_DOWN' if is_open else 'TRIA_RIGHT', icon_only=True, emboss=False)
            row.label(text=label)
            if is_open: draw_func(box.column())

        draw_box("show_base_color", "Base Color", lambda c: draw_socket_row(c, get_socket(mat, ["Base Color"])))
        draw_box("show_metallic", "Metallic", lambda c: draw_socket_row(c, get_socket(mat, ["Metallic Weight", "Metallic"])))
        draw_box("show_roughness", "Roughness", lambda c: draw_socket_row(c, get_socket(mat, ["Roughness"])))
        draw_box("show_alpha", "Alpha", lambda c: draw_socket_row(c, get_socket(mat, ["Alpha"])))
        
        def draw_normal(c):
            c.prop(scene, "compactmat_normal_mode", text="Mode")
            draw_socket_row(c, get_socket(mat, ["Normal"]), context_type="NORMAL", show_prop=False, custom_name="Map")
            
            socket = get_socket(mat, ["Normal"])
            if socket and socket.is_linked:
                node = socket.links[0].from_node
                if node.type == 'NORMAL_MAP':
                    c.prop(node.inputs['Strength'], "default_value", text="Strength")
                elif node.type == 'BUMP':
                    c.prop(node.inputs['Strength'], "default_value", text="Strength")
                    c.prop(node.inputs['Distance'], "default_value", text="Distance")
        draw_box("show_normal", "Normal", draw_normal)

        def draw_disp(c):
            if hasattr(mat, "displacement_method"):
                c.prop(mat, "displacement_method", text="Mode")
            elif hasattr(mat.cycles, "displacement_method"):
                c.prop(mat.cycles, "displacement_method", text="Mode")
                
            output = next((n for n in mat.node_tree.nodes if n.type == 'OUTPUT_MATERIAL'), None)
            if output:
                draw_socket_row(c, output.inputs.get("Displacement"), context_type="DISP", show_prop=False, custom_name="Height Map")
                
                socket = output.inputs.get("Displacement")
                if socket and socket.is_linked:
                    node = socket.links[0].from_node
                    if node.type == 'DISPLACEMENT':
                        c.prop(node.inputs['Scale'], "default_value", text="Strength")
                        c.prop(node.inputs['Midlevel'], "default_value", text="Midlevel")
            
            c.separator()
            split = c.split(factor=0.8, align=True)
            split.operator("easymat.setup_displacement", text="Setup Displacement", icon='MOD_SUBSURF')
            split.prop(ui, "disp_subdiv_levels", text="")
            
        draw_box("show_displacement", "Displacement", draw_disp)

        def draw_sss(c):
            c.prop(bsdf, "subsurface_method", text="Method")
            draw_socket_row(c, get_socket(mat, ["Subsurface Weight", "Subsurface"]), custom_name="Weight")
            draw_socket_row(c, get_socket(mat, ["Subsurface Scale"]), custom_name="Scale")
            draw_socket_row(c, get_socket(mat, ["Subsurface Radius"]), custom_name="Radius")
        draw_box("show_subsurface", "Subsurface", draw_sss)

        def draw_trans(c):
            draw_socket_row(c, get_socket(mat, ["Transmission Weight", "Transmission"]), custom_name="Weight")
            draw_socket_row(c, get_socket(mat, ["IOR"]), custom_name="IOR")
        draw_box("show_transmission", "Transmission", draw_trans)

        def draw_coat(c):
            draw_socket_row(c, get_socket(mat, ["Coat Weight", "Clearcoat"]), custom_name="Weight")
            draw_socket_row(c, get_socket(mat, ["Coat Roughness", "Clearcoat Roughness"]), custom_name="Roughness")
            draw_socket_row(c, get_socket(mat, ["Coat IOR"]), custom_name="IOR")
            draw_socket_row(c, get_socket(mat, ["Coat Tint"]), custom_name="Tint")
        draw_box("show_coat", "Coat", draw_coat)
        
        def draw_sheen(c):
            draw_socket_row(c, get_socket(mat, ["Sheen Weight", "Sheen"]), custom_name="Weight")
            draw_socket_row(c, get_socket(mat, ["Sheen Roughness"]), custom_name="Roughness")
        draw_box("show_sheen", "Sheen", draw_sheen)

        def draw_emit(c):
            draw_socket_row(c, get_socket(mat, ["Emission Color", "Emission"]), custom_name="Color")
            draw_socket_row(c, get_socket(mat, ["Emission Strength"]), custom_name="Strength")
        draw_box("show_emission", "Emission", draw_emit)

        draw_box("show_thin_film", "Thin Film", lambda c: draw_socket_row(c, get_socket(mat, ["Thin Film Thickness"])))

        mapping = mat.node_tree.nodes.get("CompactMAT_Mapping")
        if mapping and mapping.inputs.get("Scale"):
            layout.separator()
            map_box = layout.box()
            map_box.label(text="Texture Mapping", icon='TEXTURE')
            map_box.prop(mapping.inputs["Scale"], "default_value", text="Repeat UV")

# -------------------------------------------------------------------
# Registration
# -------------------------------------------------------------------

classes = (
    COMPACTMAT_OT_pick_material,
    COMPACTMAT_OT_new_material,
    COMPACTMAT_OT_remove_material,
    COMPACTMAT_OT_assign_material,
    COMPACTMAT_OT_setup_displacement,
    COMPACTMAT_OT_upload_all_textures,
    COMPACTMAT_OT_add_texture,
    COMPACTMAT_OT_remove_texture,
    CompactmatUIProps,
    COMPACTMAT_PT_main_panel,
)

def register():
    for cls in classes: bpy.utils.register_class(cls)
    bpy.types.Scene.compactmat_active_material = bpy.props.PointerProperty(
        name="Active Material", 
        type=bpy.types.Material,
        poll=poll_material
    )
    bpy.types.Scene.compactmat_ui = bpy.props.PointerProperty(type=CompactmatUIProps)
    bpy.types.Scene.compactmat_normal_mode = bpy.props.EnumProperty(
        name="Map Type",
        items=[('NORMAL', "Normal Map", "Uses a Normal Map node"), ('BUMP', "Bump Map", "Uses a Bump Map node")]
    )

def unregister():
    for cls in reversed(classes): bpy.utils.unregister_class(cls)
    del bpy.types.Scene.compactmat_active_material
    del bpy.types.Scene.compactmat_ui
    del bpy.types.Scene.compactmat_normal_mode

if __name__ == "__main__":
    register()
