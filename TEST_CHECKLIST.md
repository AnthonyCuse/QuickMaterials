# QuickMaterials Test Checklist

## UI & Window Management
- [x] Tool opens successfully in Maya
- [x] Tool can be docked to Maya UI panels
- [x] Tool can be undocked/floated
- [x] Tool remembers docked/floating state on reopen (requires userSetup.py for auto-load)
- [x] Tool window can be resized properly
- [x] Tool window maintains minimum width constraints
- [x] Tool closes without errors
- [x] Tool reopens correctly after closing

## Material Creation
- [x] Create single material with default settings
- [x] Create material with custom color
- [x] Create material with random hue enabled
- [x] Create multiple materials (one per mesh) when multiple objects selected
- [x] Create single material for all selected objects
- [x] Material creation with different shader types (standardSurface, blinn, phong, lambert, surfaceShader)
- [o] Material creation loads Arnold plugin when needed (not needed)
- [x] Material creation handles empty selection gracefully (update this to dispaly a yellow warning, match the display of the 'default settings saved' display for when no mesh is selected, we should probably do the same for any material creation. '1 material created', '5 materials created', etc. )
- [x] Material creation handles invalid selection gracefully (do the same as above, give a 'Select mesh first to apply materials' warning in viewport)

## Material Assignment
- [x] Assign material to single selected object
- [x] Assign material to multiple selected objects
- [!] Assign material to object faces (component selection) (This isnt currently possible with the tool. the tool should check for components selected and assign the material to them. the tool checks for meshes and groups selected, lets add this functionality as well.)
- [x] Assign button works from material list
- [x] Material assignment updates scene correctly

## Material Selection & Interaction
- [x] Single click selects material in list
- [x] Shift+click selects multiple materials
- [x] Ctrl+click toggles material selection
- [x] Selected materials highlight correctly in list
- [x] Material selection syncs with Maya selection
- [o] Clicking material selects objects with that material (no dont want this, its a function in the right click menu)
- [x] Double-click enters edit mode for renaming
- [x] Material selection persists when switching tabs
- [x] Material selection clears properly when clicking empty space (empty space in viewport but not in the list. would be a nice feaure)

## Material Renaming
- [x] Double-click material name to edit
- [x] Rename material with valid name
- [x] Rename material with invalid characters (handles gracefully)
- [x] Rename to existing name (handles gracefully)
- [x] Rename updates in Hypershade
- [x] Rename updates in material list immediately
- [x] Escape key cancels rename (great call, right now esc fucking closes quick materials ui altogether. lets have it not do that and also cancel renaming)
- [x] Enter key confirms rename
- [x] Clicking outside confirms rename

## Material Deletion
- [x] Delete single material (this is working but lets add a debug message of the deleted materials and a yellow viewport message, "# materials deleted, check log for details)
- [x] Delete multiple selected materials(this is working but lets add a debug message of the deleted materials and a yellow viewport message, "# materials deleted, check log for details)
- [x] Delete material removes from list
- [x] Delete material removes from Hypershade
- [x] Delete material handles materials in use gracefully
- [x] Delete default materials (lambert1, etc.) handles correctly

## Tab Switching ! (The one issue we have is closing and reopening quickmaterials with a different tab open causes issues where we default to the shaders tab and that button is checked, but also another tab button is checked, which causes issues. on quick materials reload we should default to the shaders tab.)
- [x] Switch to Shaders tab
- [x] Switch to Textures tab
- [x] Switch to Shading Groups tab
- [x] Switch to Utilities tab
- [x] Tab switching updates header label frames correctly
- [x] Tab switching maintains selection
- [x] Tab switching maintains scroll position (if applicable) (Lets implement the scroll bar staying in position on a reload)
- [x] Tab switching is smooth and responsive
- [x] Each tab shows correct content

## Filtering - General
- [x] Selected filter shows only selected materials
- [x] Used filter shows only used materials
- [x] Unused filter shows only unused materials
- [x] Referenced filter shows only referenced materials
- [x] Non-Referenced filter shows only non-referenced materials
- [x] Hide Defaults checkbox hides default materials
- [x] Multiple filters can be active simultaneously
- [x] Filter chips appear when filters are active
- [x] Clicking filter chip removes that filter
- [x] Filters work across all tabs
- [x] Filters persist when switching tabs
- [x] Filters clear properly

## Filtering - Utilities Tab Specific
- [x] Shader Utilities filter button appears on utilities tab (shader utilities might not be working. i have some place2d texture nodes that dont show bu tthey are connected to shaders. we should be sorting utilities that are conencted to existing shaders.)
- [x] Shader Utilities filter button hidden on other tabs (the shader utilities filter chip stays when switching tabs sometimes, rare, might be fine)
- [x] Shader Utilities filter chip appears when filter is active
- [x] Shader Utilities filter chip only shows on utilities tab
- [x] Connected Only mode shows only utilities connected to materials
- [x] All Utilities mode shows all utilities in scene
- [x] Toggling filter repopulates utilities list correctly
- [x] Other filters (selected, used, unused) work on utilities without repopulating (used/unused definitely doesnt work, it shows all unused. but maybe it makes sense to just hide these filter button for utilities. lets also remove the selected filter button from the utilities, jsut leaving the shader utilities and the referenced/non referecned filters. use 'selectedFilterFrame , 'usedFilterFrame', 'unusedFilterButton'. we also must ensure no utilities are hihglighted when 'highlight unused' is active
- [x] Utilities filter state persists when switching away and back

## Search Functionality
- [!] Search by material name (this works generally, but when you disable show namespaces it hides everything that appeared in the search that had namespaces but then deleting the search works in the line edit doesnt show any of the list? seems likea bug that happens when you edit the list to something matching only bc of the namespace and when u turn it off it breaks list refreshing until you refresh the list or toggle namespaces again. it jsut breaks the searching updating the list. normally you can search for materials based on their reference namespace even when its hidden so I want it to not toggle off searches using namesapce keywords when toggling off the namespace. really it ust sems like searching breaks when toggling 'hideNamespaces')
- [x] Search by partial name
- [x] Search is case-insensitive
- [x] Search updates results in real-time
- [x] Search works across all tabs
- [x] Search clears properly
- [!] Search with no results shows appropriate message (this doesnt work, it doesnt show the no 'items' found message)
- [x] Search combined with filters works correctly

## Sorting
- [x] Sort by name (ascending)
- [x] Sort by name (descending)
- [x] Sort state persists per tab (it persists globally which is what I want.. I think)
- [x] Sort state persists when switching tabs
- [x] Sort works with filters active
- [x] Sort works with search active

## Material Properties Editing
- [x] Color picker opens and works
- [x] Color changes update material immediately
- [x] Roughness slider updates material
- [x] Metalness slider updates material
- [x] Emission slider updates material
- [x] Opacity slider updates material
- [x] Transmission slider updates material
- [x] Subsurface slider updates material
- [x] Attribute visibility updates based on material type
- [x] Sliders and spinboxes stay in sync
- [x] Random hue checkbox works
- [x] Material per mesh checkbox works

## Texture Import & Management 
! (We need to make some updates to the texture importer. lets remove the 'Show Adv Textures/hide adv textures' stuff. its not necessary, lets show all by default. Lets also ensure when we use UDIMS, the texture doesnt break when we turn off udims. genrally maya switches <UDIM> to the udim number upon changing the udim type to off in the texture node, but on our import it doesnt do that for some reason, and it gets stuck on UDIM in the file texture path and so it doesnt find the non-udim textures when its disabled. lets see if we can fix this. it might require improting the texture as the real name, with the udim number, and having maya auto set it to <udim> after we do that? that might be why its breaking. We also need a set colorspace qcombobox dropdown next to the texture attribute channel checbkoxes so we can specify a different colorpsace than default if desired. have the combobox default to 'default', which will use what we have preset already.)
- [X] Import texture button works
- [!] Texture importer dialog opens (we need to update the UI for the texture attribute assignment UI that opens when textures cant automatically be assigned. make it match visually also, and add a 'Skip Import' list entry in the combo box to skip the import of that texture all together)
- [X] File textures appear in Textures tab
- [X] Procedural textures appear in Textures tab
- [X] Texture colorspace settings work
- [X] Texture preview displays correctly
- [x] Texture context menu works (ADD 'GRAPH' FUNCTION TO THIS)
- [x] Open file location works for file textures
- [x] View texture in texture viewer works

## Material Swatches & Icons
- [x] Material swatches render correctly
- [x] Swatch icons appear for all materials
- [!] Swatch rendering is performant (doesn't block UI) (Swatch rendering when texture are invovled is slow I think, we may need to rethink this, causing major slowdowns isnt good. it seems on reload/refresh its really slow, but even when no changes are made, there must be a way to cache this)
- [!] Swatch updates when material color changes (no it only updates on refresh, but the swatch does update, if we can reload just that swatch without a full list reload that would be great)
- [x] Texture icons display correctly
- [x] Procedural texture icons display correctly
- [!] Utility node icons display correctly (yes but it's laggy af, maybe we should kill the icons for the utilities or find another way to make it load instantly. the maya outliner can show all nodes instantly, hwo can we match this speed and get more performance)
- [x] Shading group icons display correctly (no icons)
- [x] Icons load asynchronously without blocking

## Context Menus
- [x] Right-click on material shows context menu
- [x] Right-click on texture shows context menu (yes, but add 'graph' function for the textures)
- [!] Context menu items are appropriate for node type (utilities dont need anything but graph and duplicate, but if it speeds up performance they could also not have a context menu)
- [x] Assign from context menu works
- [x] Select Objects from context menu works
- [x] Graph from context menu works
- [x] Duplicate from context menu works
- [x] Delete from context menu works
- [x] Batch operations work from context menu

## Graph View
- [x] Graph button opens Hypershade
- [x] Graph shows selected material
- [x] Graph shows multiple selected materials
- [x] Graph button works from context menu
- [x] Graph updates when material changes

## Material Conversion
- [x] Convert material to different shader type (this is working, but lets remove the surfaceShader conversion, that's relatively useless.)
- [!] Conversion preserves attributes where possible (preserving normals isnt working, it shows up that it can be trasnferred but we arent creating or attaching the bump2d node and making sure the normal or bump texture is reattached to the new shader)
- [x] Conversion updates material list
- [x] Conversion updates Hypershade

## File Operations
- [x] Open new Maya scene - tool handles correctly
- [x] Save scene - tool state persists
- [x] Reference files - referenced materials show correctly
- [x] Import files - imported materials show correctly

## Performance
- [!] Tool opens quickly (relatively quickly, lets ensure populating large lists doesnt cause slowdowns)
- [!] Material list populates quickly
- [x] Switching tabs is responsive
- [x] Filtering is fast
- [!] Search is responsive (searching is relatively fast, but deleting all text in the search and reloading can be slow, will need to make sure list is optimized)
- [!] Large scenes (100+ materials) perform well (its slow with many materials, will need to test and optimize. even selecting entries gets slow with lots of materials.)
- [x] Icon rendering doesn't block UI (currently doesnt seem like it does)
- [!] No memory leaks on extended use (how can this be tested?)
- [x] Utilities tab lazy loading works correctly (seems like its working but the utilities list needs major optimziation)

## Edge Cases & Error Handling
- [x] Tool handles scene with no materials
- [x] Tool handles scene with only default materials
- [x] Tool handles deleted materials gracefully
- [x] Tool handles renamed materials in Hypershade
- [x] Tool handles materials created outside tool
- [!] Tool handles corrupted material nodes (idk, how can I test this)
- [!] Tool handles missing texture files (if no file is found in a texture path, we should display the texture file name in the list as red and have a 'file not found' warning after the name)
- [x] Tool handles invalid material assignments
- [x] Tool recovers from errors gracefully
- [x] No console errors during normal operation

## Selection Synchronization
- [x] Maya selection updates material list selection
- [x] Material list selection updates Maya selection
- [x] Selection sync works with multiple materials
- [x] Selection sync works when materials are deleted
- [x] Selection sync works when switching tabs

## Settings & Preferences
- [x] Settings dialog opens
- [x] Settings can be changed
- [x] Settings persist between sessions
- [!] Texture search paths work (custom path isnt working, creating automatically is working but when I save settings it doesnt load to that set location)
- [x] Custom texture paths work (failed, explained above)

## Utilities Tab Specific
- [x] Utilities tab loads on first access (lazy loading)
- [x] Utilities tab shows loading indicator (yes, but lets make loading bar thinner)
- [x] All utility node types appear correctly (yes but its slow as fuck right now with a lot of them)
- [x] Utility icons display correctly (yes but if this is causing slowdowns we can remove and replace with a generic icon or nothing)
- [x] Utility filter works correctly
- [x] Utilities can be selected
- [x] Utilities can be graphed 
- [x] Utilities context menu works (no, get rid of everything but graph and duplicate (and duplciate will need to be a sepcific duplicate to just duplicate the node rather than shading group), but again we can get rid of all if performance will improve)
- [x] Utilities tab doesn't repopulate unnecessarily

## Shading Groups Tab
- [x] Shading groups appear in tab
- [o] Shading group icons display (blue rounded squares) (we removed this, unnecesary)
- [x] Shading groups can be selected
- [x] Shading groups can be graphed (this works but attempts to graph all shapes connected into it, we need this to be a separate graphing than materials and only graph the shading rgoup and attached shaders to avoid this problem)
- [x] Shading groups context menu works (it works we just need to have only select objs and graph which need to be custom. select objs for shading groups needs to select all objects that are being used by all sahders using that group. graph needs to graph all sahders using that group)

## Textures Tab
- [!] File textures appear in tab (these dont appear upon creating a file texture node)
- [!] Procedural textures appear in tab (these dont appear upon creating a proc texture node)
- [x] Texture colorspace displays correctly
- [x] Texture preview displays correctly
- [x] Textures can be selected
- [x] Textures can be graphed (textrues and procedural nodes need a custom graph function. proc nodes will only have graph. file textures we need to add graph to existing functions )
- [x] Texture context menu works (update with explained changes)

## Batch Operations
- [x] Select objects from multiple materials
- [x] Graph multiple materials
- [x] Duplicate multiple materials
- [x] Delete multiple materials
- [x] Batch operations work correctly

## Visual Styling
- [x] Material list styling is correct
- [!] Selected materials highlight correctly (unused highlighted materials need to highlight a bit when hovered over)
- [!] Unused materials highlight correctly (red) (working but see above)
- [X] File textures have yellow tint
- [x] Procedural textures have grey styling
- [x] Shading groups have blue tint
- [x] Utilities have grey styling
- [!] Hover states work correctly (need to fix to highlight a slightly lighter red when we have highlight unused enabled)
- [x] Edit mode styling is correct
- [x] Filter chips display correctly 

## Integration with Maya
- [x] Tool works with Maya 2024 (PySide2)
- [!] Tool works with Maya 2025+ (PySide6) (works but some stylesheet issues, will resolve later)
- [x] Tool integrates with Hypershade
- [x] Tool integrates with Maya selection
- [x] Tool works with Maya references
- [x] Tool works with Maya namespaces
- [x] Tool handles Maya scene changes

## Refresh & Updates
- [x] Refresh button updates material list
- [!] Material list updates when materials added in Hypershade (yes but reminder here to fix icons for certain materials. have a default blank icon if there is none but ensure the list entry is still aligned. use the dispalcement idcon for dispalcement nodes rather than a blank shader ball)
- [x] Material list updates when materials deleted in Hypershade
- [x] Material list updates when materials renamed in Hypershade
- [!] Auto-refresh works correctly (we need to look into this, the refreshing occurs too often and sometimes causes major slowdowns. adding or duplciating meshes forces a refresh, thats problematic, we need to focus our refreshes only when an action occurs that affects the current list)
- [x] Manual refresh works correctly

## Workspace Control
- [x] Tool can be saved to shelf
- [x] Tool can be opened from shelf
- [x] Tool workspace control name is correct
- [x] Tool can be restored from workspace

## Advanced Features
- [x] Material per mesh mode works
- [x] Random hue generation works
- [x] Color picker integration works
- [x] Texture viewer integration works
- [!] Arnold plugin loading works (idk if it loads automatically, we use a standardsurface though as default so it shouldnt be that necessary)
- [x] Material type detection works correctly

## Regression Tests
- [] All previously working features still work
- [] No new errors introduced
- [] Performance hasn't degraded
- [] UI hasn't broken

## Stress Tests
- [] Create 100+ materials
- [] Switch tabs rapidly
- [] Apply filters rapidly
- [] Search with many materials
- [] Select/deselect rapidly
- [] Open/close tool multiple times

