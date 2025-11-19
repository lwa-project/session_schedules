#!/usr/bin/env python3
"""
Patch file for sessionGUI_tk.py
This contains the missing/incomplete methods that need to be added.
Apply these changes to complete the Tkinter migration.
"""

# =============================================================================
# PATCH 1: Replace SDFCreator.on_edit() method (currently just "pass")
# Location: In SDFCreator class, around line 1400
# =============================================================================

def on_edit_REPLACEMENT(self, item, column, value):
    """
    Handle cell edits from the ObservationTreeview.
    This method performs validation and updates the observation data.

    Args:
        item: Treeview item ID
        column: Column index (0-based)
        value: New value entered by user

    Returns:
        bool: True if edit was successful, False otherwise
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


# =============================================================================
# PATCH 2: Add after_edit() method to ObservationTreeview class
# Location: In ObservationTreeview class, after __init__
# =============================================================================

def after_edit_FOR_ObservationTreeview(self, item, column, value):
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


# =============================================================================
# PATCH 3: Add after_edit() method to SteppedTreeview class
# Location: In SteppedTreeview class, after __init__
# =============================================================================

def after_edit_FOR_SteppedTreeview(self, item, column, value):
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


# =============================================================================
# PATCH 4: Enhanced on_validate() for SDFCreator class (OPTIONAL - enhances color coding)
# Location: Replace the current on_validate() method in SDFCreator
# =============================================================================

def on_validate_ENHANCED(self, event=None, confirmValid=True):
    """
    Enhanced validation that colors invalid rows red.
    This replaces the simplified version.
    """
    # Check for bad edits first
    if hasattr(self, 'badEdit') and self.badEdit:
        messagebox.showerror("Validation Error",
                           "Please fix the cell editing error before validating.")
        return False

    # Capture validation output
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    sys.stdout = StringIO()
    sys.stderr = StringIO()

    try:
        # Validate each observation
        all_valid = True
        for i, obs in enumerate(self.project.sessions[0].observations):
            try:
                # Validate the observation
                is_valid = obs.validate(verbose=True)

                # Get the treeview item
                items = self.listControl.get_children()
                if i < len(items):
                    item = items[i]

                    if is_valid:
                        # Color valid observations black
                        self.listControl.item(item, tags=())
                    else:
                        # Color invalid observations red
                        self.listControl.item(item, tags=('invalid',))
                        all_valid = False

            except Exception as e:
                pid_print(f"Error validating observation {i+1}: {str(e)}")
                items = self.listControl.get_children()
                if i < len(items):
                    item = items[i]
                    self.listControl.item(item, tags=('invalid',))
                all_valid = False

        # Validate the entire project
        try:
            project_valid = self.project.validate(verbose=True)
        except Exception as e:
            pid_print(f"Error validating project: {str(e)}")
            project_valid = False

        # Get validation output
        stdout_output = sys.stdout.getvalue()
        stderr_output = sys.stderr.getvalue()

        # Restore stdout/stderr
        sys.stdout = old_stdout
        sys.stderr = old_stderr

        # Print validation output
        if stdout_output:
            print(stdout_output)
        if stderr_output:
            print(stderr_output, file=sys.stderr)

        # Show results
        final_valid = all_valid and project_valid

        if confirmValid:
            if final_valid:
                messagebox.showinfo("Validation Results",
                                  "All observations are valid!")
            else:
                messagebox.showerror("Validation Errors",
                                   "Validation failed. Invalid observations are marked in red.\n\n" +
                                   "Check the console output for details.")

        return final_valid

    except Exception as e:
        # Restore stdout/stderr
        sys.stdout = old_stdout
        sys.stderr = old_stderr

        messagebox.showerror("Validation Error",
                           f"Error during validation:\n\n{str(e)}")
        return False


# =============================================================================
# PATCH 5: Add tag configuration to create_listview() in SDFCreator
# Location: At the end of SDFCreator.create_listview() method
# =============================================================================

def configure_tags_FOR_listControl(listControl):
    """
    Configure tags for highlighting invalid rows.
    Add this at the end of create_listview() method:

        # Configure tags for error highlighting
        self.listControl.tag_configure('error', foreground='red')
        self.listControl.tag_configure('invalid', foreground='red')
    """
    listControl.tag_configure('error', foreground='red')
    listControl.tag_configure('invalid', foreground='red')


# =============================================================================
# PATCH 6: HelpWindow wrapper function
# Location: Add at module level, before if __name__ == "__main__":
# =============================================================================

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


# =============================================================================
# PATCH 7: Fix SteppedWindow.create_widgets() import error
# Location: In SteppedWindow.create_widgets() method
# =============================================================================

# REMOVE this line (around line 3800):
#     from stepped_treeview import SteppedTreeview
#
# The SteppedTreeview class is already defined in the same file at the top.
# Just use it directly:
#     self.listControl = SteppedTreeview(main_frame)


# =============================================================================
# USAGE INSTRUCTIONS
# =============================================================================

"""
To apply these patches to your sessionGUI_tk.py framework:

1. Replace on_edit() method in SDFCreator class with on_edit_REPLACEMENT
2. Add after_edit_FOR_ObservationTreeview to ObservationTreeview class
3. Add after_edit_FOR_SteppedTreeview to SteppedTreeview class
4. (Optional) Replace on_validate() with on_validate_ENHANCED for better error highlighting
5. Add the two tag_configure lines at the end of create_listview()
6. Add the HelpWindow() function at module level
7. Remove the "from stepped_treeview import SteppedTreeview" line

These changes will complete the framework and make it fully functional!
"""
