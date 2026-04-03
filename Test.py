from pywinauto import Application

app = Application(backend="uia").connect(title_re=".*Spark.*")
win = app.top_window()

print("TITLE:", win.window_text())
win.print_control_identifiers()