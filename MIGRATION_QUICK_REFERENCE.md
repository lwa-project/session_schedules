# Tkinter Migration Quick Reference Guide

## Critical Classes to Convert (in priority order)

### Priority 1: Core Data Model Classes (Lower Complexity)
1. **ObserverInfo** (598 lines) - Dialog for observer/project metadata
2. **VolumeInfo** (98 lines) - Data volume calculator
3. **ResolveTarget** (148 lines) - Target name resolver
4. **ScheduleWindow** (92 lines) - Scheduling options
5. **HelpWindow** (30 lines) - Help display

### Priority 2: Complex List Controls (VERY HIGH Complexity)
1. **ObservationListCtrl** (153 lines) - Editable list with checkboxes & dropdowns
   - **Mixins**: TextEditMixin, CheckListCtrlMixin, custom ChoiceMixIn
   - **Challenge**: Inline cell editing, dropdown selection, checkboxes
   - **Tkinter Equivalent**: ttk.Treeview with custom cell editor
   - **Effort**: 3-4 days

2. **SteppedListCtrl** (79 lines) - Similar to ObservationListCtrl
   - **Effort**: 2-3 days

3. **ChoiceMixIn** (134 lines) - Dropdown cell editor for lists
   - **Challenge**: Platform-specific scrolling, focus management
   - **Tkinter Equivalent**: Custom Toplevel dropdown widget
   - **Effort**: 2-3 days

### Priority 3: Dialog Classes (Medium Complexity)
1. **AdvancedInfo** (864 lines) - Hardware/MCS settings
   - **Challenge**: Many ComboBox/TextCtrl widgets with interdependencies
   - **Effort**: 2 days

2. **SessionDisplay** (295 lines) - Matplotlib plotting
   - **Challenge**: Matplotlib integration, canvas resize handling
   - **Tkinter Equivalent**: TkAgg backend
   - **Effort**: 1-2 days (mostly working)

### Priority 4: Main Application (VERY HIGH Complexity)
1. **SDFCreator** (1,533 lines) - Main window
   - **Challenges**:
     - 45+ methods with complex interdependencies
     - 30+ event handlers
     - Dynamic column generation
     - File I/O integration
     - Menu state management
   - **Effort**: 4-5 days

---

## Key Tkinter Components Needed

| wxPython | Tkinter | Effort |
|----------|---------|--------|
| wx.Frame | tk.Toplevel / tk.root | Easy |
| wx.ListCtrl | ttk.Treeview | Medium |
| wx.TextCtrl | tk.Entry / tk.Text | Easy |
| wx.ComboBox | ttk.Combobox | Easy |
| wx.Choice | tk.OptionMenu / ttk.Combobox | Easy |
| wx.Button | tk.Button | Easy |
| wx.StaticText | tk.Label | Easy |
| wx.CheckBox | tk.Checkbutton | Easy |
| wx.RadioButton | tk.Radiobutton | Easy |
| wx.Panel | tk.Frame | Easy |
| wx.GridBagSizer | Grid geometry manager | Easy |
| wx.BoxSizer | Pack/Grid geometry managers | Easy |
| wx.FileDialog | tkinter.filedialog | Easy |
| wx.MessageDialog | tkinter.messagebox | Easy |
| TextEditMixin | Custom cell editor | HARD |
| CheckListCtrlMixin | Custom checkbox column | HARD |
| ChoiceMixIn | Custom dropdown widget | HARD |

---

## Data Validation Layer (Don't Lose This!)

These conversion functions MUST be preserved:

```python
# In addColumns() - Lines 1577-1678
raConv(text)        # RA HH:MM:SS → decimal hours (0-24)
decConv(text)       # Dec DD:MM:SS → decimal degrees (-90 to 90)
freqConv(text)      # Frequency MHz → Hz (hardware validated)
freqOptConv(text)   # Optional frequency (0 allowed)
filterConv(text)    # Filter code → int (1-7)
snrConv(text)       # Boolean string → Python bool
```

**Don't move validation into UI!** Keep separate validation/coercion layer.

---

## Event Binding Requirements

Must support these 12 event types:

1. **wx.EVT_MENU** → tk menu command callback
2. **wx.EVT_BUTTON** → tk button command callback
3. **wx.EVT_CHOICE** → ttk.Combobox <<ComboboxSelected>>
4. **wx.EVT_RADIOBUTTON** → tk.Radiobutton variable callback
5. **wx.EVT_CHECKBOX** → tk.Checkbutton variable callback
6. **wx.EVT_CLOSE** → protocol("WM_DELETE_WINDOW")
7. **wx.EVT_LIST_END_LABEL_EDIT** → Custom Treeview edit event
8. **wx.EVT_KILL_FOCUS** → tk.Focus events
9. **wx.EVT_SIZE** → tk.Configure event
10. **wx.EVT_IDLE** → tk.after_idle()
11. **wx.EVT_PAINT** → tk.Canvas <<Configure>>
12. **wx.EVT_MOTION** → tk.Motion event

---

## wxPython Version Compatibility (Lines 46-68)

**NOT NEEDED IN TKINTER!** Delete the compatibility layer:

```python
# DELETE THESE LINES:
if 'phoenix' in wx.PlatformInfo:
    AppendMenuItem = lambda x, y: x.Append(y)
    # ... 15 more compatibility functions
```

**Simple Tkinter**: Just use standard API everywhere.

---

## Critical Methods to Understand (in complexity order)

### Lowest Complexity (1 day each)
- `onNew()`, `onLoad()`, `onSave()`, `onSaveAs()`
- `onCut()`, `onCopy()`, `onPasteBefore()`, `onPasteAfter()`, `onPasteEnd()`
- `onAddTBW()`, `onAddTBF()`, `onAddTBN()`, `onAddDRXR/S/J/L()`
- `onRemove()`, `onQuit()`
- `VolumeInfo.initUI()`, `ResolveTarget.initUI()`

### Medium Complexity (2-3 days each)
- `addObservation()` - Format observation for display
- `setSaveButton()` - Toggle save state
- `setMenuButtons()` - Enable/disable menus by mode
- `displayError()` - Show error dialogs
- All ObserverInfo/AdvancedInfo methods

### High Complexity (3-5 days each)
- **`onEdit()`** (lines 1322-1363) - Cell edit with validation
  - Coerces value using coerceMap
  - Updates project object
  - Refreshes display
  
- **`onValidate()`** (lines 1408-1463) - Complex validation loop
  - Colors rows RED/BLACK
  - Captures stdout
  - Shows dialogs
  
- **`addColumns()`** (lines 1571-1768) - Dynamic column generation
  - Contains 6 nested conversion functions
  - Mode-dependent columns (TBW/TBF/TBN/DRX)
  
- **`parseFile()`** (lines 1992-2046) - File parsing
  - Loads XML/text SDF
  - Recreates project structure
  - Determines mode

- **`SDFCreator.initUI()`** (lines 619-806) - 200 lines of menu/toolbar setup
  - 25+ menu items
  - 15+ toolbar buttons
  - List control initialization

---

## Platform-Specific Issues to Handle

### Lines 127-152 (OpenDropdown in ChoiceMixIn)
```python
if wx.Platform == "__WXMSW__":
    # Windows: Auto-scroll with dropdown
else:
    # Linux: Manual scroll required
```

**Tkinter**: Probably won't need platform-specific code here

### Lines 47-68 (Compatibility layer)
```python
if 'phoenix' in wx.PlatformInfo:
    # Phoenix API
else:
    # Classic API
```

**Tkinter**: DELETE - not needed

### Line 4174 (GTK2 fonts)
```python
if "gtk2" in wx.PlatformInfo:
    self.SetStandardFonts()
```

**Tkinter**: DELETE - not needed

---

## File I/O Dependencies

**NOT CHANGING** (external library):
- `from lsl.common import sdf, sdfADP, sdfNDP` 
- Project structure from LSL
- All observation validation via `observation.validate()`

**YOU NEED**:
- Keep column mapping (`columnMap`, `coerceMap`)
- Keep data flow from LSL objects to display
- Keep validation logic separate

---

## Testing Checkpoints (Recommended Order)

1. **Week 1**: All dialog classes (ObserverInfo, VolumeInfo, etc.)
2. **Week 2**: SteppedListCtrl and ChoiceMixIn cell editor
3. **Week 3**: ObservationListCtrl with full editing
4. **Week 4**: SDFCreator main window
5. **Week 5**: Integration testing with full SDF workflow

---

## Copy/Paste Buffer Implementation

**Current**: Uses Python pickle on observation objects

```python
# SDFCreator
self.buffer = None

# In onCopy():
self.buffer = copy.deepcopy(self.project.sessions[0].observations[index])

# In onPasteBefore():
obs_copy = copy.deepcopy(self.buffer)
self.project.sessions[0].observations.insert(index, obs_copy)
```

**For Tkinter**: Same approach works fine! LSL observation objects are picklable.

---

## Menu Structure to Replicate

### File Menu
- New (Ctrl+N)
- Open (Ctrl+O)
- Save (Ctrl+S)
- Save As
- Quit

### Edit Menu
- Cut Selected Observation (Ctrl+X)
- Copy Selected Observation (Ctrl+C)
- Paste Before Selected (Ctrl+V)
- Paste After Selected
- Paste at End of List

### Observations Menu
- Observer/Project Info
- Scheduling
- Add → TBW, TBF, TBN, DRX (RA/Dec, Solar, Jovian, Lunar), Stepped (RA/Dec, Az/Alt)
- Edit Selected Stepped Obs
- Remove Selected
- Validate All (F5)
- Resolve Selected (F3)
- Session at a Glance
- Advanced Settings

### Data Menu
- Estimated Data Volume

### Help Menu
- Session GUI Handbook (F1)
- Filter Codes
- About

---

## Toolbar Buttons (15 total)

Row 1: New, Open, Save, SaveAs, Quit
Row 2: Add TBW, Add TBF, Add TBN
Row 3: Add DRX (RA/Dec, Solar, Jovian, Lunar)
Row 4: Add Stepped (RA/Dec, Az/Alt), Edit Stepped
Row 5: Remove, Validate
Row 6: Help

---

## Status Bar Requirements

- Status message display
- Updates on file save, validation, etc.

---

## Icon Resource Directory

All icons in: `./icons/` subdirectory
- new.png, open.png, save.png, save-as.png, exit.png
- tbw.png, tbf.png, tbn.png
- drx-radec.png, drx-solar.png, drx-jovian.png, drx-lunar.png
- stepped-radec.png, stepped-azalt.png, stepped-edit.png
- remove.png, validate.png, help.png
- lwa.png (for about dialog)
- tooltip.png (for gain help tooltip)

---

## Configuration File Location

- **Path**: `~/.sessionGUI`
- **Purpose**: Store observer preferences
- **Contents**:
  ```
  ObserverID <id>
  ObserverFirstName <name>
  ObserverLastName <name>
  ProjectID <id>
  ProjectName <name>
  ```

---

## Session Mode Determination Logic

After ObserverInfo dialog, determine mode:

```python
# If any TBW observations exist:
mode = 'TBW'

# Else if any TBF observations exist:
mode = 'TBF'

# Else if any TBN observations exist:
mode = 'TBN'

# Else (including DRX observations):
mode = 'DRX'
```

**Important**: Mode changes trigger `addColumns()` to rebuild the list!

---

## Key Constants to Define

```python
# File menu
ID_NEW = 11
ID_OPEN = 12
ID_SAVE = 13
ID_SAVE_AS = 14
ID_QUIT = 16

# Observation menu
ID_INFO = 21
ID_SCHEDULE = 22
ID_ADD_TBW = 23
# ... etc (see lines 503-540)

# List control
ID_LISTCTRL = 71

# Edit menu
ID_CUT = 81
ID_COPY = 82
ID_PASTE_BEFORE = 83
ID_PASTE_AFTER = 84
ID_PASTE_END = 85

# Dialog IDs (ObserverInfo)
ID_OBS_INFO_OK = 501
ID_OBS_INFO_CANCEL = 502
ID_OBS_INFO_DEFAULTS = 503
ID_OBS_INFO_DRSPEC = 504
```

---

## Effort Estimate Summary

| Component | Lines | Effort | Risk |
|-----------|-------|--------|------|
| Dialog Classes (Observers, Volume, Resolve, Schedule, Help) | 1,366 | 4 days | Low |
| SessionDisplay (with Matplotlib) | 295 | 2 days | Low-Medium |
| AdvancedInfo | 864 | 2 days | Medium |
| ChoiceMixIn & SteppedListCtrl | 213 | 3 days | HIGH |
| ObservationListCtrl | 153 | 3 days | VERY HIGH |
| SDFCreator main window | 1,533 | 5 days | VERY HIGH |
| **TOTAL** | **4,723** | **19 days** | **MEDIUM-HIGH** |

**Key Risk**: List control complexity (multiple mixins, cell editing, checkboxes)

---

## Files to Modify

- `/home/user/session_schedules/sessionGUI.py` - Main file (4,723 lines)
- Keep `/home/user/session_schedules/icons/` - Icon resources unchanged
- Keep `/home/user/session_schedules/docs/help.html` - Help doc unchanged
- Keep other dependencies unchanged (LSL, conflict, etc.)

---

## Testing Strategy

1. **Unit tests** for conversion functions (raConv, decConv, etc.)
2. **Dialog tests** for each window class
3. **Integration tests** for data flow
4. **File I/O tests** (load/save SDF)
5. **Platform tests** (Windows, Linux)

---

## Migration Checklist

- [ ] Create Tkinter main window (SDFCreator equivalent)
- [ ] Create ttk.Treeview for observation list
- [ ] Implement custom cell editor with dropdowns
- [ ] Implement checkbox column for Treeview
- [ ] Create all 8 dialog windows
- [ ] Implement menu system (File, Edit, Observations, Data, Help)
- [ ] Implement toolbar
- [ ] Data validation layer (conversion functions)
- [ ] File I/O (load/save SDF)
- [ ] Matplotlib integration (SessionDisplay)
- [ ] Event handler bindings
- [ ] Color coding for validation (RED/BLACK)
- [ ] Copy/paste buffer
- [ ] Preferences file (`~/.sessionGUI`)
- [ ] Cross-platform testing

