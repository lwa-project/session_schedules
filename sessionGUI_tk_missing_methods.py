# Missing methods to add to sessionGUI_tk.py
# These should be integrated into the main file

# =============================================================================
# Missing method for SDFCreator class
# =============================================================================

def on_edit(self, item, column, value):
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
        print(f"Error: {str(err)}")
        self.statusbar.config(text=f"Error: {str(err)}")

        # Mark the item with error tag
        self.listControl.item(item, tags=('error',))

        # Show error dialog
        messagebox.showerror("Validation Error",
                           f"Invalid value for {self.listControl.heading(f'#{obsAttr+1}', 'text')}:\n\n{str(err)}")

        return False


def on_validate_enhanced(self, event=None, confirmValid=True):
    """
    Enhanced validation that colors invalid rows red.
    This replaces the simplified on_validate() in the framework.
    """
    # Capture validation output
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
                item = self.listControl.get_children()[i]

                if is_valid:
                    # Color valid observations black
                    self.listControl.item(item, tags=())
                else:
                    # Color invalid observations red
                    self.listControl.item(item, tags=('invalid',))
                    all_valid = False

            except Exception as e:
                print(f"Error validating observation {i+1}: {str(e)}")
                item = self.listControl.get_children()[i]
                self.listControl.item(item, tags=('invalid',))
                all_valid = False

        # Validate the entire project
        try:
            project_valid = self.project.validate(verbose=True)
        except Exception as e:
            print(f"Error validating project: {str(e)}")
            project_valid = False

        # Get validation output
        stdout_output = sys.stdout.getvalue()
        stderr_output = sys.stderr.getvalue()

        # Restore stdout/stderr
        sys.stdout = sys.__stdout__
        sys.stderr = sys.__stderr__

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
        sys.stdout = sys.__stdout__
        sys.stderr = sys.__stderr__

        messagebox.showerror("Validation Error",
                           f"Error during validation:\n\n{str(e)}")
        return False


# =============================================================================
# Enhanced EditableCell connection
# =============================================================================

def after_edit_for_observation_treeview(self, item, column, value):
    """
    Override of after_edit() for ObservationTreeview to call parent's on_edit().
    This should be added to the ObservationTreeview class.
    """
    if hasattr(self, 'parent') and hasattr(self.parent, 'on_edit'):
        return self.parent.on_edit(item, column, value)
    else:
        # Default behavior - just update the value
        values = list(self.item(item, 'values'))
        values[column] = value
        self.item(item, values=values)
        return True


# =============================================================================
# Simple HelpWindow wrapper
# =============================================================================

def HelpWindow(parent):
    """
    Simple wrapper to create a help window.
    Opens the help documentation file.
    """
    help_file = os.path.join(parent.scriptPath, 'docs', 'help.html')

    if os.path.exists(help_file):
        if HTML_SUPPORT:
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
# Tag configuration for error highlighting
# =============================================================================

def configure_treeview_tags(treeview):
    """
    Configure tags for the treeview to highlight errors and invalid rows.
    This should be called after creating the ObservationTreeview.
    """
    treeview.tag_configure('error', foreground='red')
    treeview.tag_configure('invalid', foreground='red')
    treeview.tag_configure('valid', foreground='black')


# =============================================================================
# Fix for SteppedWindow import
# =============================================================================

# In SteppedWindow.__init__(), remove this line:
#   from stepped_treeview import SteppedTreeview
#
# The SteppedTreeview class is already defined in the same file,
# so it doesn't need to be imported.

# In the create_widgets() method of SteppedWindow, change:
#   from stepped_treeview import SteppedTreeview
# to just use the class directly:
#   self.listControl = SteppedTreeview(main_frame)
