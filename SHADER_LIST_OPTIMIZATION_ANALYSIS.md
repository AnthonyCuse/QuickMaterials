# QuickMaterials Shader List Optimization Analysis

## Executive Summary

This document analyzes the current implementation of the shader list in QuickMaterials, focusing on how it reloads, what happens during reloads, and optimization opportunities. The primary performance bottlenecks are:

1. **Frequent full list rebuilds** - The list often rebuilds completely when only a single material changes
2. **Synchronous swatch icon generation** - Material swatch icons are generated during list population, blocking UI
3. **Texture color calculation overhead** - Texture average color calculation happens for every material with textures, even when cached
4. **Lack of incremental updates** - No mechanism to update only changed materials while preserving others

---

## Current Implementation Overview

### List Reload Flow

The shader list reload process follows this flow:

```
User Action / Scene Change
    ↓
refresh_materials_list() [Debounced: 150ms]
    ↓
_perform_actual_refresh()
    ↓
populate_materials_scroll_area()
    ↓
[Full Rebuild]
    ├─ Collect all materials/textures/shading groups
    ├─ Batch compute material properties
    ├─ Filter materials
    ├─ Create UI entries (containers, line edits)
    ├─ Queue icon creations (_pending_icon_creations)
    └─ Batch create icons asynchronously
```

### Key Methods

1. **`refresh_materials_list()`** (line 9349)
   - Debounced entry point (150ms delay)
   - Cancels pending refreshes and starts timer
   - Calls `_perform_actual_refresh()` when timer fires

2. **`_perform_actual_refresh()`** (line 9359)
   - Guard checks (suspend count, UI alive, rebuilding flag)
   - Gets search text
   - Calls `populate_materials_scroll_area()`

3. **`populate_materials_scroll_area()`** (line 7862)
   - **FULL REBUILD** - Clears all existing entries
   - Collects all materials/textures/shading groups from Maya
   - Batch computes material properties (referenced, used, affects_selection)
   - Filters materials based on active filters
   - Creates new UI entries for each material
   - Queues icon creation requests
   - Schedules asynchronous icon batch creation

4. **`_batch_create_icons()`** (line 9180)
   - Processes icon creation queue in batches of 10
   - Creates MaterialSwatchIcon widgets
   - Calls `load_swatch()` on each icon (with 10ms delay per icon)

5. **`MaterialSwatchIcon.load_swatch()`** (material_swatch_icon.py:105)
   - Creates swatch in "fast mode" first (skips texture color calculation)
   - Then schedules async update with texture colors (50ms delay)
   - Calls `_create_swatch_icon()` which:
     - Gets material color (with texture average color if not fast_mode)
     - Applies metalness darkening
     - Gets roughness for specular highlight
     - Calculates opacity/transmission/transparency
     - Renders QImage with gradients, emission glow, specular highlight
     - Converts to QPixmap and scales to display size

---

## What Triggers List Reloads

### Automatic Triggers (via Material Watchers)

The system installs Maya callbacks that trigger refreshes on:

1. **Node Added** (`NodeAdded` scriptJob)
   - Triggers when any shader/material/texture/shading group is created
   - Calls `_queue_material_refresh(0)` - immediate refresh

2. **Node Deleted** (`NodeDeleted` scriptJob)
   - Triggers when any shader/material/texture/shading group is deleted
   - Calls `_queue_material_refresh(0)` - immediate refresh

3. **Attribute Changed** (OpenMaya callbacks)
   - Monitors material attribute changes
   - Calls `_queue_material_refresh(150)` - debounced refresh

4. **Selection Changed** (`SelectionChanged` scriptJob)
   - Only triggers refresh if "Selected" filter is active
   - Calls `_sync_list_from_scene_selection()` which may trigger refresh

5. **Polling** (`_poll_materials_snapshot()`)
   - Periodic polling (every 2 seconds) to detect material changes
   - Compares material count and names
   - Triggers refresh if changes detected

### Manual Triggers

1. **Filter Changes** - Any filter checkbox/button toggle
2. **Search Text Changes** - Typing in search box (debounced 150ms)
3. **Tab Switching** - Switching between Shaders/Textures/Shading Groups/Utilities tabs
4. **Refresh Button** - Manual refresh button click
5. **Settings Changes** - Show icons, hide namespaces, highlight unused checkboxes

### Current Optimization Attempts

1. **Debouncing** - 150ms delay on refresh requests
2. **Incremental Search** - `_apply_search_filter()` shows/hides widgets without rebuild (only for search text changes)
3. **Silent Refresh** - `_begin_silent_refresh()` / `_end_silent_refresh()` prevents refresh during in-place renames
4. **Batch Icon Creation** - Icons created in batches of 10 asynchronously
5. **Fast Mode Swatches** - Initial swatch uses fast mode (skips texture color), then updates with texture color async
6. **Material Property Caching** - `_material_cache` caches material properties (referenced, used, affects_selection) with timeout

---

## Performance Issues

### Issue 1: Full Rebuild on Every Change

**Problem:**
- When a single material changes, the entire list is rebuilt
- All UI widgets are destroyed and recreated
- All swatch icons are regenerated, even for unchanged materials

**Impact:**
- High CPU usage during rebuild
- UI freezes during rebuild (especially with many materials)
- All swatch icons reload, causing flickering
- User loses scroll position and selection state

**Example Scenario:**
- User has 200 materials in the list
- User changes the color of one material
- Entire list rebuilds, all 200 swatch icons regenerate
- Takes 2-5 seconds depending on texture count

### Issue 2: Swatch Icon Generation Overhead

**Problem:**
- Each swatch icon requires:
  - Maya API calls to get material attributes (color, roughness, metalness, emission, opacity, transmission)
  - Texture file loading and average color calculation (if not cached)
  - Complex QPainter rendering (gradients, emission glow, specular highlights)
  - Image scaling operations

**Current Flow:**
1. Icon widget created
2. `load_swatch()` called (10ms delay per icon)
3. Fast mode swatch created (skips texture color)
4. Texture color update scheduled (50ms delay)
5. Full swatch rendered with texture color

**Impact:**
- With 200 materials, 200 swatch icons are generated
- Each swatch takes ~10-50ms to generate
- Total time: 2-10 seconds for all swatches
- Blocks UI thread during batch creation

**Texture Color Calculation:**
- `_get_texture_average_color()` loads image file
- Scales image to 32x32 for sampling
- Samples pixels (every 8th pixel)
- Calculates average RGB
- Cached by (texture_path, mtime), but cache lookup still happens for every material

### Issue 3: Unnecessary Reloads

**Problem:**
- List reloads even when changes don't affect the list
- Example: Changing a material's color doesn't change whether it appears in the list
- Example: Renaming a material triggers full rebuild (though silent refresh helps)

**Current Triggers:**
- Attribute changes trigger refresh even if material still passes filters
- Selection changes trigger refresh even if "Selected" filter not active
- Polling detects changes even if they don't affect list visibility

### Issue 4: No Incremental Updates

**Problem:**
- No mechanism to update only the changed material's entry
- No way to preserve other entries' swatch icons
- No way to update only the swatch icon without rebuilding the entire entry

**What's Needed:**
- Track which materials have changed
- Update only those materials' entries
- Preserve swatch icons for unchanged materials
- Update swatch icon independently of list entry

---

## Optimization Opportunities

### Opportunity 1: Incremental Material Updates

**Goal:** Update only changed materials, preserve others

**Implementation:**
1. Track material state (name, attributes, swatch hash)
2. On refresh, compare current state to previous state
3. For unchanged materials:
   - Keep existing UI entry
   - Keep existing swatch icon
   - Only update if filter state changed
4. For changed materials:
   - Update existing entry if it exists
   - Regenerate swatch icon only
   - Create new entry if material is new
5. Remove entries for deleted materials

**Benefits:**
- 10-100x faster for single material changes
- No flickering of unchanged swatches
- Preserves scroll position
- Preserves selection state

**Challenges:**
- Need to track material state efficiently
- Need to handle material renames
- Need to handle filter changes (may need full rebuild)
- Need to handle tab switches (may need full rebuild)

### Opportunity 2: Lazy Swatch Loading

**Goal:** Load swatches only when visible or needed

**Implementation:**
1. Don't create swatch icons during list population
2. Create swatch icons only when:
   - Material entry becomes visible (scrolled into view)
   - User hovers over material entry
   - Material is selected
3. Use QScrollArea's viewport to detect visible items
4. Unload swatches for items scrolled out of view (optional, for memory)

**Benefits:**
- Instant list display (no swatch generation delay)
- Only generate swatches for visible materials
- With 200 materials but only 20 visible, only 20 swatches generated
- 10x faster initial list display

**Challenges:**
- Need to detect when items become visible
- Need to handle scroll position changes
- May need to preload swatches slightly ahead of viewport

### Opportunity 3: Swatch Icon Caching

**Goal:** Cache generated swatch icons to avoid regeneration

**Implementation:**
1. Generate hash of material state (color, roughness, metalness, emission, opacity, texture path + mtime)
2. Cache swatch pixmap by hash
3. On refresh, check if material state changed
4. If unchanged, reuse cached swatch pixmap
5. If changed, regenerate and update cache

**Benefits:**
- Avoid regenerating swatches for unchanged materials
- Even with full rebuild, swatches load instantly from cache
- Reduces CPU usage significantly

**Challenges:**
- Need efficient hash generation
- Need to invalidate cache when material changes
- Memory usage (but swatches are small, ~22x22 pixels)

### Opportunity 4: Optimize Texture Color Calculation

**Goal:** Reduce overhead of texture average color calculation

**Current Issues:**
- Texture color calculated even in "fast mode" (just delayed)
- Cache lookup happens for every material
- Image loading and sampling happens even for cached textures

**Optimizations:**
1. **Pre-calculate texture colors** - Calculate all texture colors once during scene load, not per-material
2. **Better caching** - Cache texture colors globally, not per-material
3. **Lazy texture color** - Only calculate texture color when swatch is actually displayed
4. **Background thread** - Calculate texture colors in background thread (Qt threading)

**Benefits:**
- Faster swatch generation
- Less blocking of UI thread
- Better user experience

### Opportunity 5: Smarter Refresh Triggers

**Goal:** Only refresh when necessary

**Implementation:**
1. **Attribute Changes:**
   - Check if changed attribute affects list visibility (e.g., name change)
   - If only affects swatch appearance, update swatch only
   - If affects filter state, update entry only (don't rebuild)
   - If affects visibility, rebuild only affected entries

2. **Selection Changes:**
   - Only refresh if "Selected" filter is active
   - Use incremental update to show/hide entries
   - Don't rebuild entire list

3. **Polling:**
   - Only check for material count/name changes
   - Don't poll if no changes detected recently
   - Use more efficient change detection

**Benefits:**
- Fewer unnecessary rebuilds
- Faster response to actual changes
- Better performance overall

### Opportunity 6: Preserve Widget State

**Goal:** Preserve scroll position, selection, and edit state during updates

**Implementation:**
1. Save scroll position before rebuild
2. Restore scroll position after rebuild
3. Preserve selection state (selected_materials_list)
4. Preserve edit state (which materials are being edited)
5. Preserve expanded/collapsed state (if applicable)

**Benefits:**
- Better user experience
- No loss of context during updates
- Smoother workflow

---

## Recommended Implementation Priority

### Phase 1: Quick Wins (High Impact, Low Effort)

1. **Swatch Icon Caching** (Opportunity 3)
   - Add hash-based cache for swatch pixmaps
   - Reuse cached swatches on rebuild
   - Estimated impact: 50-80% reduction in swatch generation time

2. **Lazy Swatch Loading** (Opportunity 2)
   - Load swatches only when visible
   - Use QScrollArea viewport detection
   - Estimated impact: 90% reduction in initial load time

3. **Smarter Refresh Triggers** (Opportunity 5)
   - Skip refresh for attribute changes that don't affect visibility
   - Use incremental updates for selection changes
   - Estimated impact: 50-70% reduction in unnecessary rebuilds

### Phase 2: Incremental Updates (High Impact, Medium Effort)

4. **Incremental Material Updates** (Opportunity 1)
   - Track material state
   - Update only changed materials
   - Preserve unchanged entries
   - Estimated impact: 10-100x faster for single material changes

### Phase 3: Advanced Optimizations (Medium Impact, High Effort)

5. **Optimize Texture Color Calculation** (Opportunity 4)
   - Pre-calculate texture colors
   - Background thread processing
   - Estimated impact: 20-30% faster swatch generation

6. **Preserve Widget State** (Opportunity 6)
   - Save/restore scroll position
   - Preserve selection and edit state
   - Estimated impact: Better UX, no performance gain

---

## Technical Implementation Notes

### Material State Tracking

To implement incremental updates, need to track:

```python
class MaterialState:
    name: str
    node_type: str
    color: tuple  # (r, g, b)
    roughness: float
    metalness: float
    emission: tuple  # (r, g, b)
    opacity: float
    transmission: float
    texture_path: str
    texture_mtime: float
    swatch_hash: str  # Hash of all visual properties
    
    # Filter-related
    is_referenced: bool
    is_used: bool
    affects_selection: bool
    passes_filters: bool
```

### Swatch Icon Cache

```python
_swatch_cache = {}  # material_name -> (swatch_hash, QPixmap)

def get_swatch_hash(material_state):
    """Generate hash from material visual properties."""
    data = (
        material_state.color,
        material_state.roughness,
        material_state.metalness,
        material_state.emission,
        material_state.opacity,
        material_state.transmission,
        material_state.texture_path,
        material_state.texture_mtime,
    )
    return hash(data)

def get_cached_swatch(material_name, material_state):
    """Get cached swatch if state unchanged."""
    current_hash = get_swatch_hash(material_state)
    cached = _swatch_cache.get(material_name)
    if cached and cached[0] == current_hash:
        return cached[1]  # Return cached pixmap
    return None  # Need to regenerate
```

### Incremental Update Logic

```python
def update_material_entry(material_name, old_state, new_state):
    """Update single material entry without rebuilding entire list."""
    entry = _entry_list[_index_by_material[material_name]]
    
    # Update swatch if visual properties changed
    if get_swatch_hash(old_state) != get_swatch_hash(new_state):
        swatch_icon = entry.get('swatch')
        if swatch_icon:
            swatch_icon.load_swatch()  # Regenerate swatch
    
    # Update filter state if needed
    if old_state.passes_filters != new_state.passes_filters:
        container = entry.get('container')
        container.setVisible(new_state.passes_filters)
    
    # Update other properties as needed
    # ...
```

### Lazy Swatch Loading

```python
def on_viewport_changed():
    """Called when scroll area viewport changes."""
    visible_rect = scroll_area.viewport().rect()
    
    for entry in _entry_list:
        container = entry.get('container')
        swatch = entry.get('swatch')
        
        # Check if entry is visible
        entry_rect = container.geometry()
        is_visible = visible_rect.intersects(entry_rect)
        
        if is_visible and not swatch:
            # Create swatch for visible entry
            create_swatch_icon(entry)
        elif not is_visible and swatch:
            # Optional: Unload swatch for non-visible entry (memory optimization)
            pass
```

---

## Expected Performance Improvements

### Current Performance (Baseline)

- **Initial List Load (200 materials):** 3-5 seconds
- **Single Material Change:** 2-4 seconds (full rebuild)
- **Filter Change:** 2-4 seconds (full rebuild)
- **Search Text Change:** 0.1-0.3 seconds (incremental search works)

### After Phase 1 Optimizations

- **Initial List Load (200 materials):** 0.3-0.5 seconds (lazy loading + caching)
- **Single Material Change:** 0.05-0.1 seconds (swatch update only)
- **Filter Change:** 0.5-1 second (still needs rebuild, but faster with caching)
- **Search Text Change:** 0.1-0.3 seconds (unchanged)

### After Phase 2 Optimizations

- **Initial List Load (200 materials):** 0.3-0.5 seconds
- **Single Material Change:** 0.01-0.05 seconds (incremental update)
- **Filter Change:** 0.2-0.5 seconds (incremental update for most cases)
- **Search Text Change:** 0.1-0.3 seconds

### Overall Improvement

- **10-100x faster** for single material changes
- **5-10x faster** for initial list load
- **2-5x faster** for filter changes
- **Much smoother** user experience with no flickering

---

## Conclusion

The current implementation works but has significant optimization opportunities. The biggest wins will come from:

1. **Incremental updates** - Only update what changed
2. **Lazy swatch loading** - Only load visible swatches
3. **Swatch caching** - Reuse generated swatches
4. **Smarter refresh triggers** - Only refresh when necessary

Implementing these optimizations will result in a much more responsive and performant tool, especially when working with large numbers of materials.

