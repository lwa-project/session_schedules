# SessionGUI Tkinter Framework - Integration Instructions

This document provides step-by-step instructions to integrate the missing methods into your Tkinter framework.

## Quick Summary

Your framework is **95% complete**! You just need to add 7 small patches to make it fully functional.

## Files Needed

- Your framework code (from your message - save as `sessionGUI_user_framework.py`)
- `sessionGUI_tk_patch.py` - Contains all the missing methods

---

## Step-by-Step Integration

### PATCH 1: Add `after_edit()` to ObservationTreeview

**Location**: In `ObservationTreeview` class, after the `__init__` method

**Add this method**:
```python
def after_edit(self, item, column, value):
    """
    Override of after_edit() to call parent's on_edit() for validation.
    This connects the EditableCell editing to the SDFCreator validation.
    """
    if hasattr(self, 'parent') and hasattr(self.parent, 'on_edit'):
        return self.parent.on_edit(item, column, value)
    else:
        # Default behavior - just update the value
        values = list(self.item(item, 'values'))
        if 0 <= column < len(values):
            values[column] = value
            self.item(item, values=values)
        return True
```

---

### PATCH 2: Add `after_edit()` to SteppedTreeview

**Location**: In `SteppedTreeview` class, after the `__init__` method

**Add this method**:
```python
def after_edit(self, item, column, value):
    """
    Override of after_edit() for SteppedTreeview to call parent's on_edit().
    """
    if hasattr(self, 'parent') and hasattr(self.parent, 'on_edit'):
        return self.parent.on_edit(item, column, value)
    else:
        # Default behavior - just update the value
        values = list(self.item(item, 'values'))
        if 0 <= column < len(values):
            values[column] = value
            self.item(item, values=values)
        return True
```

---

### PATCH 3: Replace SDFCreator.on_edit() (CRITICAL!)

**Location**: In `SDFCreator` class, around line 1400

**Find this**:
```python
def on_edit(self, event=None):
    pass
```

**Replace with**:
```python
def on_edit(self, item, column, value):
    """
    Handle cell edits from the ObservationTreeview.
    This method performs validation and updates the observation data.
    """
    # Get the observation index from the item
    obsIndex = self.listControl.index(item)
    obsAttr = column

    # Clear status bar
    self.statusbar.config(text='')

    try:
        # Coerce the value using the appropriate conversion function
        newData = self.coerceMap[obsAttr](value)

        # Get the current value
        oldData = getattr(self.project.sessions[0].observations[obsIndex],
                         self.columnMap[obsAttr])

        # Only update if the value has changed
        if newData != oldData:
            # Update the observation object
            setattr(self.project.sessions[0].observations[obsIndex],
                   self.columnMap[obsAttr], newData)
            self.project.sessions[0].observations[obsIndex].update()

            # Update the display
            values = list(self.listControl.item(item, 'values'))
            values[obsAttr] = value
            self.listControl.item(item, values=values)

            # Mark as edited
            self.edited = True
            self.set_save_button()

        # Clear any error tags
        if 'error' in self.listControl.item(item, 'tags'):
            self.listControl.item(item, tags=())

        return True

    except ValueError as err:
        # Display error
        pid_print(f"Error: {str(err)}")
        self.statusbar.config(text=f"Error: {str(err)}")

        # Mark the item with error tag
        self.listControl.item(item, tags=('error',))

        # Show error dialog
        messagebox.showerror("Validation Error",
                           f"Invalid value:\n\n{str(err)}")

        return False
```

---

### PATCH 4: Add tag configuration to create_listview()

**Location**: In `SDFCreator.create_listview()` method, at the very end (after packing the listControl)

**Add these lines**:
```python
# Configure tags for error highlighting
self.listControl.tag_configure('error', foreground='red')
self.listControl.tag_configure('invalid', foreground='red')
```

---

### PATCH 5: Fix SteppedWindow import error

**Location**: In `SteppedWindow.create_widgets()` method

**Find and REMOVE this line**:
```python
from stepped_treeview import SteppedTreeview
```

The `SteppedTreeview` class is already defined in the same file, so no import is needed.

---

### PATCH 6: Add HelpWindow wrapper function

**Location**: At module level, just BEFORE the `if __name__ == "__main__":` block

**Add this function**:
```python
def HelpWindow(parent):
    """
    Create and display the help window.
    This is a wrapper function to maintain compatibility.
    """
    help_file = os.path.join(parent.scriptPath, 'docs', 'help.html')

    if os.path.exists(help_file):
        if HAS_HTML:
            window = HtmlHelpWindow(parent,
                                   title="Session GUI Handbook",
                                   html_file=help_file,
                                   size=(800, 600))
        else:
            window = SimpleHtmlWindow(parent,
                                     title="Session GUI Handbook",
                                     html_file=help_file,
                                     size=(800, 600))
    else:
        # Fallback if help file doesn't exist
        messagebox.showinfo("Help",
                          f"Help file not found: {help_file}\n\n" +
                          "Please check the documentation online at:\n" +
                          "http://lwa.unm.edu")
        window = None

    return window
```

---

### PATCH 7: (OPTIONAL) Enhanced validation with color-coding

**Location**: In `SDFCreator` class

This patch is optional but recommended. It enhances the `on_validate()` method to color invalid rows red.

**Find the current `on_validate()` method and replace it with the enhanced version from `sessionGUI_tk_patch.py`**.

The enhanced version:
- Captures validation output
- Colors invalid rows red
- Colors valid rows black
- Shows detailed error messages

---

## Testing After Integration

After applying all patches, test the following:

### Test 1: Cell Editing
1. Launch the application
2. Create a new session (File → New)
3. Fill in observer info
4. Add an observation
5. Double-click a cell to edit it
6. Enter an invalid value (e.g., negative frequency)
   - **Expected**: Error message, cell marked red
7. Enter a valid value
   - **Expected**: Cell updates, no error

### Test 2: Validation
1. Add several observations with some invalid values
2. Press F5 or Observations → Validate All
   - **Expected**: Invalid rows turn red, error dialog shows

### Test 3: File Operations
1. Save the session (File → Save As)
2. Close and reopen the file (File → Open)
   - **Expected**: All observations reload correctly

### Test 4: Help
1. Press F1 or Help → Session GUI Handbook
   - **Expected**: Help window opens (if help.html exists)

---

## Verification Checklist

- [ ] Added `after_edit()` to ObservationTreeview
- [ ] Added `after_edit()` to SteppedTreeview
- [ ] Replaced `on_edit()` in SDFCreator
- [ ] Added tag configuration to `create_listview()`
- [ ] Removed invalid import from SteppedWindow
- [ ] Added HelpWindow() wrapper function
- [ ] (Optional) Enhanced on_validate() method
- [ ] Tested cell editing
- [ ] Tested validation
- [ ] Tested file operations

---

## Common Issues and Fixes

### Issue: "IndexError: list index out of range" when editing
**Fix**: Make sure the columnMap and coerceMap are properly initialized in add_columns()

### Issue: Cells don't respond to double-click
**Fix**: Verify that EditableCell's __init__() is being called and after_edit() is properly overridden

### Issue: Validation doesn't show errors
**Fix**: Check that tag_configure was added to create_listview()

### Issue: "NameError: name 'SteppedTreeview' is not defined"
**Fix**: Make sure you removed the incorrect import line

---

## Summary

These 7 patches complete your Tkinter framework:

1. ✅ Connect ObservationTreeview editing to validation
2. ✅ Connect SteppedTreeview editing to validation
3. ✅ Implement cell editing with validation (CRITICAL)
4. ✅ Enable error highlighting with tags
5. ✅ Fix import error
6. ✅ Add help window support
7. ✅ (Optional) Enhanced validation display

After integration, you'll have a fully functional Tkinter version of sessionGUI!
