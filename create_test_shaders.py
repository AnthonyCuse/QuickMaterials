import maya.cmds as cmds
import random
import math

"""
Create Test Shaders
-------------------
Creates 100 unique shaders in Maya to test the shader swatch viewer.
This script generates various material types with different properties:
- StandardSurface materials with various colors, roughness, metalness, emission, opacity, transmission
- Legacy shaders (Lambert, Blinn, Phong)
- Materials with colored opacity/transmission
"""

def create_test_shaders():
    """Create 100 unique test shaders with various properties."""
    
    # Clear existing test shaders if they exist
    existing_shaders = cmds.ls("TestShader_*", materials=True)
    if existing_shaders:
        cmds.delete(existing_shaders)
        print(f"[TestShaders] Deleted {len(existing_shaders)} existing test shaders")
    
    shaders = []
    
    # Color palettes for variety
    base_colors = [
        (1.0, 0.0, 0.0),  # Red
        (0.0, 1.0, 0.0),  # Green
        (0.0, 0.0, 1.0),  # Blue
        (1.0, 1.0, 0.0),  # Yellow
        (1.0, 0.0, 1.0),  # Magenta
        (0.0, 1.0, 1.0),  # Cyan
        (1.0, 0.5, 0.0),  # Orange
        (0.5, 0.0, 1.0),  # Purple
        (0.8, 0.8, 0.8),  # Light Grey
        (0.2, 0.2, 0.2),  # Dark Grey
        (1.0, 0.8, 0.6),  # Skin tone
        (0.6, 0.4, 0.2),  # Brown
        (0.9, 0.9, 1.0),  # Off-white
        (0.1, 0.1, 0.2),  # Dark blue-grey
        (0.4, 0.8, 0.4),  # Light green
        (0.8, 0.4, 0.4),  # Light red
    ]
    
    emission_colors = [
        (1.0, 0.5, 0.5),  # Warm red emission
        (0.5, 1.0, 0.5),  # Warm green emission
        (0.5, 0.5, 1.0),  # Warm blue emission
        (1.0, 1.0, 0.5),  # Yellow emission
        (1.0, 0.8, 0.8),  # Pink emission
        (0.8, 1.0, 1.0),  # Cyan emission
        (1.0, 1.0, 1.0),  # White emission
        (1.0, 0.7, 0.3),  # Orange emission
    ]
    
    tint_colors = [
        (1.0, 0.5, 0.8),  # Pink tint
        (0.5, 0.8, 1.0),  # Blue tint
        (0.8, 1.0, 0.5),  # Green tint
        (1.0, 0.9, 0.6),  # Yellow tint
        (0.9, 0.7, 1.0),  # Purple tint
        (1.0, 0.7, 0.7),  # Red tint
    ]
    
    def get_color_name(color):
        """Get a descriptive name for a color."""
        if color == (1.0, 0.0, 0.0):
            return "Red"
        elif color == (0.0, 1.0, 0.0):
            return "Green"
        elif color == (0.0, 0.0, 1.0):
            return "Blue"
        elif color == (1.0, 1.0, 0.0):
            return "Yellow"
        elif color == (1.0, 0.0, 1.0):
            return "Magenta"
        elif color == (0.0, 1.0, 1.0):
            return "Cyan"
        elif color == (1.0, 0.5, 0.0):
            return "Orange"
        elif color == (0.5, 0.0, 1.0):
            return "Purple"
        elif color[0] > 0.7 and color[1] > 0.7 and color[2] > 0.7:
            return "Light"
        elif color[0] < 0.3 and color[1] < 0.3 and color[2] < 0.3:
            return "Dark"
        else:
            return "Mixed"
    
    def get_roughness_name(roughness):
        """Get a descriptive name for roughness."""
        if roughness < 0.05:
            return "Mirror"
        elif roughness < 0.15:
            return "Shiny"
        elif roughness < 0.4:
            return "SemiGloss"
        elif roughness < 0.6:
            return "Matte"
        else:
            return "Rough"
    
    def get_tint_name(tint_color):
        """Get a descriptive name for tint color."""
        if tint_color == (1.0, 0.5, 0.8):
            return "Pink"
        elif tint_color == (0.5, 0.8, 1.0):
            return "Blue"
        elif tint_color == (0.8, 1.0, 0.5):
            return "Green"
        elif tint_color == (1.0, 0.9, 0.6):
            return "Yellow"
        elif tint_color == (0.9, 0.7, 1.0):
            return "Purple"
        elif tint_color == (1.0, 0.7, 0.7):
            return "Red"
        return "Colored"
    
    # Shader 1-50: StandardSurface materials
    for i in range(1, 51):
        name_parts = ["TestShader"]
        
        # Base color variations
        if i <= 16:
            # Basic colors
            color = base_colors[(i - 1) % len(base_colors)]
            color_name = get_color_name(color)
            name_parts.append(color_name)
        elif i <= 30:
            # Random colors
            color = (random.random(), random.random(), random.random())
            color_name = "Random"
            name_parts.append(color_name)
        else:
            # Mixed variations
            base_idx = (i - 1) % len(base_colors)
            base_color = base_colors[base_idx]
            # Add some variation
            color = (
                max(0.0, min(1.0, base_color[0] + (random.random() - 0.5) * 0.3)),
                max(0.0, min(1.0, base_color[1] + (random.random() - 0.5) * 0.3)),
                max(0.0, min(1.0, base_color[2] + (random.random() - 0.5) * 0.3))
            )
            color_name = get_color_name(base_color)
            name_parts.append(color_name + "Var")
        
        # Create StandardSurface
        shader = cmds.shadingNode("standardSurface", asShader=True, name="temp_shader")
        shaders.append(shader)
        
        # Set base color
        cmds.setAttr(f"{shader}.baseColor", color[0], color[1], color[2], type="double3")
        
        # Roughness variations
        roughness = 0.5
        if i % 10 == 1:
            roughness = 0.0  # Very shiny
            name_parts.append("Mirror")
        elif i % 10 == 2:
            roughness = 0.1  # Shiny
            name_parts.append("Shiny")
        elif i % 10 == 3:
            roughness = 0.3  # Semi-gloss
            name_parts.append("SemiGloss")
        elif i % 10 == 4:
            roughness = 0.5  # Matte
            name_parts.append("Matte")
        elif i % 10 == 5:
            roughness = 0.8  # Rough
            name_parts.append("Rough")
        else:
            roughness = random.random() * 0.9  # Random
            name_parts.append(get_roughness_name(roughness))
        cmds.setAttr(f"{shader}.specularRoughness", roughness)
        
        # Metalness variations
        metalness = 0.0
        if i % 7 == 1:
            metalness = 1.0  # Full metal
            name_parts.append("Metal")
        elif i % 7 == 2:
            metalness = 0.5  # Semi-metal
            name_parts.append("SemiMetal")
        elif i % 7 == 3:
            metalness = 0.0  # Non-metal
        else:
            metalness = random.random()  # Random
            if metalness > 0.5:
                name_parts.append("Metal")
        cmds.setAttr(f"{shader}.metalness", metalness)
        
        # Emission variations (shaders 11-25)
        if 11 <= i <= 25:
            emission_idx = (i - 11) % len(emission_colors)
            emission_color = emission_colors[emission_idx]
            emission_intensity = 0.3 + random.random() * 1.5  # 0.3 to 1.8
            emission_name = get_color_name(emission_color)
            if emission_intensity > 1.0:
                name_parts.append(f"{emission_name}EmissionHi")
            else:
                name_parts.append(f"{emission_name}Emission")
            
            cmds.setAttr(f"{shader}.emission", emission_intensity)
            cmds.setAttr(f"{shader}.emissionColor", 
                        emission_color[0], emission_color[1], emission_color[2], 
                        type="double3")
        
        # Opacity variations (shaders 26-35)
        if 26 <= i <= 35:
            opacity_value = 0.2 + (i - 26) * 0.08  # 0.2 to 1.0
            opacity_percent = int(opacity_value * 100)
            name_parts.append(f"Opacity{opacity_percent}")
            cmds.setAttr(f"{shader}.opacity", opacity_value, opacity_value, opacity_value, type="double3")
        
        # Colored opacity (shaders 36-40)
        if 36 <= i <= 40:
            tint_idx = (i - 36) % len(tint_colors)
            tint_color = tint_colors[tint_idx]
            opacity_value = 0.3 + (i - 36) * 0.15  # 0.3 to 0.9
            tint_name = get_tint_name(tint_color)
            opacity_percent = int(opacity_value * 100)
            name_parts.append(f"{tint_name}Opacity{opacity_percent}")
            cmds.setAttr(f"{shader}.opacity", 
                        tint_color[0] * opacity_value,
                        tint_color[1] * opacity_value,
                        tint_color[2] * opacity_value,
                        type="double3")
        
        # Transmission variations (shaders 41-45)
        if 41 <= i <= 45:
            transmission_value = 0.2 + (i - 41) * 0.2  # 0.2 to 1.0
            transmission_percent = int(transmission_value * 100)
            name_parts.append(f"Transmission{transmission_percent}")
            cmds.setAttr(f"{shader}.transmission", transmission_value)
        
        # Colored transmission (shaders 46-50)
        if 46 <= i <= 50:
            tint_idx = (i - 46) % len(tint_colors)
            tint_color = tint_colors[tint_idx]
            transmission_value = 0.8 + (i - 46) * 0.05  # 0.8 to 1.0
            tint_name = get_tint_name(tint_color)
            transmission_percent = int(transmission_value * 100)
            name_parts.append(f"{tint_name}Transmission{transmission_percent}")
            cmds.setAttr(f"{shader}.transmission", transmission_value)
            cmds.setAttr(f"{shader}.transmissionColor", 
                        tint_color[0], tint_color[1], tint_color[2], 
                        type="double3")
        
        # Create final name
        shader_name = "_".join(name_parts)
        cmds.rename(shader, shader_name)
    
    # Shaders 51-70: Legacy Lambert materials
    for i in range(51, 71):
        name_parts = ["TestShader", "Lambert"]
        
        shader = cmds.shadingNode("lambert", asShader=True, name="temp_shader")
        shaders.append(shader)
        
        # Base color
        color_idx = (i - 51) % len(base_colors)
        color = base_colors[color_idx]
        color_name = get_color_name(color)
        name_parts.append(color_name)
        cmds.setAttr(f"{shader}.color", color[0], color[1], color[2], type="double3")
        
        # Incandescence (emission) for some
        if i % 3 == 0:
            emission_idx = (i - 51) % len(emission_colors)
            emission_color = emission_colors[emission_idx]
            emission_intensity = 0.3 + random.random() * 0.7
            emission_name = get_color_name(emission_color)
            name_parts.append(f"{emission_name}Incandescence")
            cmds.setAttr(f"{shader}.incandescence", 
                        emission_color[0] * emission_intensity,
                        emission_color[1] * emission_intensity,
                        emission_color[2] * emission_intensity,
                        type="double3")
        
        # Transparency for some
        if i % 4 == 0:
            transparency_value = 0.2 + random.random() * 0.6
            transparency_percent = int(transparency_value * 100)
            name_parts.append(f"Transparency{transparency_percent}")
            cmds.setAttr(f"{shader}.transparency", 
                        transparency_value, transparency_value, transparency_value,
                        type="double3")
        
        # Colored transparency for some
        if i % 5 == 0 and i > 60:
            tint_idx = (i - 61) % len(tint_colors)
            tint_color = tint_colors[tint_idx]
            transparency_value = 0.3 + random.random() * 0.5
            tint_name = get_tint_name(tint_color)
            transparency_percent = int(transparency_value * 100)
            name_parts.append(f"{tint_name}Transparency{transparency_percent}")
            cmds.setAttr(f"{shader}.transparency", 
                        tint_color[0] * transparency_value,
                        tint_color[1] * transparency_value,
                        tint_color[2] * transparency_value,
                        type="double3")
        
        # Create final name
        shader_name = "_".join(name_parts)
        cmds.rename(shader, shader_name)
    
    # Shaders 71-85: Blinn materials
    for i in range(71, 86):
        name_parts = ["TestShader", "Blinn"]
        
        shader = cmds.shadingNode("blinn", asShader=True, name="temp_shader")
        shaders.append(shader)
        
        # Base color
        color_idx = (i - 71) % len(base_colors)
        color = base_colors[color_idx]
        color_name = get_color_name(color)
        name_parts.append(color_name)
        cmds.setAttr(f"{shader}.color", color[0], color[1], color[2], type="double3")
        
        # Eccentricity (roughness equivalent)
        eccentricity = 0.5
        if i % 5 == 1:
            eccentricity = 0.0  # Very shiny
            name_parts.append("Mirror")
        elif i % 5 == 2:
            eccentricity = 0.2  # Shiny
            name_parts.append("Shiny")
        elif i % 5 == 3:
            eccentricity = 0.5  # Semi-gloss
            name_parts.append("SemiGloss")
        else:
            eccentricity = 0.8  # Rough
            name_parts.append("Rough")
        cmds.setAttr(f"{shader}.eccentricity", eccentricity)
        
        # Specular roll off
        rolloff = 0.3 + random.random() * 0.5
        cmds.setAttr(f"{shader}.specularRollOff", rolloff)
        
        # Incandescence for some
        if i % 3 == 0:
            emission_idx = (i - 71) % len(emission_colors)
            emission_color = emission_colors[emission_idx]
            emission_intensity = 0.2 + random.random() * 0.8
            emission_name = get_color_name(emission_color)
            name_parts.append(f"{emission_name}Incandescence")
            cmds.setAttr(f"{shader}.incandescence", 
                        emission_color[0] * emission_intensity,
                        emission_color[1] * emission_intensity,
                        emission_color[2] * emission_intensity,
                        type="double3")
        
        # Create final name
        shader_name = "_".join(name_parts)
        cmds.rename(shader, shader_name)
    
    # Shaders 86-100: Phong materials
    for i in range(86, 101):
        name_parts = ["TestShader", "Phong"]
        
        shader = cmds.shadingNode("phong", asShader=True, name="temp_shader")
        shaders.append(shader)
        
        # Base color
        color_idx = (i - 86) % len(base_colors)
        color = base_colors[color_idx]
        color_name = get_color_name(color)
        name_parts.append(color_name)
        cmds.setAttr(f"{shader}.color", color[0], color[1], color[2], type="double3")
        
        # Cosine power (shininess - inverse of roughness)
        cosine_power = 20.0
        if i % 5 == 1:
            cosine_power = 100.0  # Very shiny
            name_parts.append("Mirror")
        elif i % 5 == 2:
            cosine_power = 50.0  # Shiny
            name_parts.append("Shiny")
        elif i % 5 == 3:
            cosine_power = 20.0  # Semi-gloss
            name_parts.append("SemiGloss")
        else:
            cosine_power = 5.0  # Rough
            name_parts.append("Rough")
        cmds.setAttr(f"{shader}.cosinePower", cosine_power)
        
        # Incandescence for some
        if i % 4 == 0:
            emission_idx = (i - 86) % len(emission_colors)
            emission_color = emission_colors[emission_idx]
            emission_intensity = 0.3 + random.random() * 1.0
            emission_name = get_color_name(emission_color)
            name_parts.append(f"{emission_name}Incandescence")
            cmds.setAttr(f"{shader}.incandescence", 
                        emission_color[0] * emission_intensity,
                        emission_color[1] * emission_intensity,
                        emission_color[2] * emission_intensity,
                        type="double3")
        
        # Transparency for some
        if i % 3 == 0:
            transparency_value = 0.1 + random.random() * 0.7
            transparency_percent = int(transparency_value * 100)
            name_parts.append(f"Transparency{transparency_percent}")
            cmds.setAttr(f"{shader}.transparency", 
                        transparency_value, transparency_value, transparency_value,
                        type="double3")
        
        # Create final name
        shader_name = "_".join(name_parts)
        cmds.rename(shader, shader_name)
    
    print(f"[TestShaders] Created {len(shaders)} test shaders:")
    print(f"  - StandardSurface: 50 shaders (colors, roughness, metalness, emission, opacity, transmission)")
    print(f"  - Lambert: 20 shaders (colors, incandescence, transparency)")
    print(f"  - Blinn: 15 shaders (colors, eccentricity, specular, incandescence)")
    print(f"  - Phong: 15 shaders (colors, cosine power, incandescence, transparency)")
    print(f"\n[TestShaders] All shaders prefixed with 'TestShader_' are ready for testing!")
    
    return shaders


if __name__ == "__main__":
    create_test_shaders()

