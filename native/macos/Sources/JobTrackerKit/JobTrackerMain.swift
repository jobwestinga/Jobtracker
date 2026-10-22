import AppKit
import SwiftUI

public enum JobTrackerMain {
    static let launchStarted = Date()

    /// Startup phase log for performance evidence (JOBTRACKER_NATIVE_TIMING=1).
    static func mark(_ phase: String) {
        guard ProcessInfo.processInfo.environment["JOBTRACKER_NATIVE_TIMING"] == "1" else { return }
        FileHandle.standardError.write(Data(String(format: "timing %.3f %@\n", Date().timeIntervalSince(launchStarted), phase).utf8))
    }

    public static func run() {
        _ = launchStarted
        // Start the engine before AppKit finishes launching so Python startup,
        // recovery and the first reads overlap window creation.
        let journalFallback = FileManager.default.temporaryDirectory.appendingPathComponent("jobtracker-native-journal")
        var configuration: EngineConfiguration?
        var configurationError: String?
        do { configuration = try EngineConfiguration.resolve() } catch { configurationError = error.localizedDescription }
        let launch = configuration?.launch ?? EngineLaunch(executable: URL(fileURLWithPath: "/usr/bin/false"), arguments: [])
        let engine = EngineClient(launch: launch, journal: RequestJournal(directory: configuration?.journalDirectory ?? journalFallback))
        let model = AppModel(engine: engine, configuration: configuration)
        if let configurationError { engine.fail(configurationError) } else { engine.start() }
        mark("engine-started")

        let app = NSApplication.shared
        let delegate = AppDelegate(model: model)
        app.delegate = delegate
        app.setActivationPolicy(.regular)
        withExtendedLifetime(delegate) { app.run() }
    }
}

/// Owns the single window, the engine lifecycle and window-level input.
final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    private var window: NSWindow!
    private let model: AppModel
    private var keyRouter: KeyRouter?
    private var quitApproved = false
    private var appearanceObserver: Any?
    private var capture: CaptureRunner?

    init(model: AppModel) {
        self.model = model
        super.init()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        JobTrackerMain.mark("did-finish-launching")
        buildMenu()

        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 680, height: 920),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
                          backing: .buffered, defer: false)
        window.title = Bundle.main.infoDictionary?["CFBundleDisplayName"] as? String ?? "JobTracker"
        window.titlebarAppearsTransparent = true
        window.minSize = NSSize(width: CGFloat(DesignTokens.lookup(DesignTokens.shared.roles.density, "windowMinWidth") ?? 560),
                                height: CGFloat(DesignTokens.lookup(DesignTokens.shared.roles.density, "windowMinHeight") ?? 600))
        window.collectionBehavior = [.fullScreenPrimary]
        window.isReleasedWhenClosed = false
        window.delegate = self
        window.setFrameAutosaveName("JobTrackerMainWindow")
        window.contentView = NSHostingView(rootView: RootView(app: model))
        if !window.setFrameUsingName("JobTrackerMainWindow") { window.center() }
        applyAppearance()
        appearanceObserver = model.$theme.sink { [weak self] _ in DispatchQueue.main.async { self?.applyAppearance() } }
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        JobTrackerMain.mark("window-shown")

        keyRouter = KeyRouter(model: model, window: window)
        if let directory = ProcessInfo.processInfo.environment["JOBTRACKER_NATIVE_CAPTURE"] {
            capture = CaptureRunner(model: model, window: window, directory: URL(fileURLWithPath: directory))
            capture?.start()
        }
    }

    /// The window follows the chosen theme, not the system light/dark setting.
    private func applyAppearance() {
        let theme = model.effectiveTheme
        window.appearance = NSAppearance(named: theme.bgPrimary.luminance > 0.4 ? .aqua : .darkAqua)
        window.backgroundColor = theme.bgPrimary.nsColor
    }

    func applicationDidBecomeActive(_ notification: Notification) {
        Task { @MainActor in await model.foregroundChanged(true) }
    }

    func applicationDidResignActive(_ notification: Notification) {
        Task { @MainActor in await model.foregroundChanged(false) }
    }

    func windowDidMiniaturize(_ notification: Notification) { model.foreground = false }
    func windowDidDeminiaturize(_ notification: Notification) { model.foreground = NSApp.isActive }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func windowShouldClose(_ sender: NSWindow) -> Bool {
        if quitApproved { return true }
        NSApp.terminate(nil)
        return false
    }

    /// Normal quit follows the Qt rules: refuse while a session is genuinely
    /// tracking; otherwise the engine pushes briefly, writes the rotating JSON
    /// backup and exits. A backup failure is reported but never traps the user.
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if quitApproved { return .terminateNow }
        guard model.engine.isReady else {
            model.engine.stop()
            _ = model.engine.waitUntilExit(timeout: 8)
            return .terminateNow
        }
        Task { @MainActor in
            guard let result = await model.requestQuit() else {
                model.show("Quit was not confirmed by the engine. Your current session and pending work are kept.", error: true)
                NSApp.reply(toApplicationShouldTerminate: false)
                return
            }
            if !result.quit {
                window.makeKeyAndOrderFront(nil)
                model.select(.subjects)
                model.panels.removeAll { if case .confirm = $0.kind { return true }; return false }
                model.present(.confirm(ConfirmModel(title: result.title ?? "Session Still Active",
                                                    message: result.message ?? "JobTracker cannot quit while a session is active.",
                                                    confirmTitle: "OK", role: .primary, action: {})))
                NSApp.reply(toApplicationShouldTerminate: false)
                return
            }
            if let error = result.backupError {
                NSLog("JobTracker auto-backup on quit failed: %@", error)
            }
            model.engine.stop()
            _ = model.engine.waitUntilExit(timeout: 8)
            quitApproved = true
            NSApp.reply(toApplicationShouldTerminate: true)
        }
        return .terminateLater
    }

    private func buildMenu() {
        let main = NSMenu()
        let appItem = NSMenuItem()
        main.addItem(appItem)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "About JobTracker", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Settings\u{2026}", action: #selector(openSettings), keyEquivalent: ",").target = self
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Hide JobTracker", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        appMenu.addItem(withTitle: "Quit JobTracker", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu

        let editItem = NSMenuItem()
        main.addItem(editItem)
        let edit = NSMenu(title: "Edit")
        edit.addItem(withTitle: "Undo", action: #selector(undoAction(_:)), keyEquivalent: "z").target = self
        edit.addItem(.separator())
        edit.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = edit

        let windowItem = NSMenuItem()
        main.addItem(windowItem)
        let windowMenu = NSMenu(title: "Window")
        windowMenu.addItem(withTitle: "Minimize", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        windowMenu.addItem(withTitle: "Zoom", action: #selector(NSWindow.performZoom(_:)), keyEquivalent: "")
        windowMenu.addItem(withTitle: "Enter Full Screen", action: #selector(NSWindow.toggleFullScreen(_:)), keyEquivalent: "f").keyEquivalentModifierMask = [.command, .control]
        windowItem.submenu = windowMenu
        NSApp.windowsMenu = windowMenu
        NSApp.mainMenu = main
    }

    @objc private func openSettings() { model.openSettings() }

    /// Text fields keep their own text undo; elsewhere Cmd+Z reverses the one
    /// recorded app action, like the Qt window.
    @objc private func undoAction(_ sender: Any?) {
        if let editor = window.firstResponder as? NSTextView, editor.undoManager?.canUndo == true {
            editor.undoManager?.undo()
            return
        }
        guard model.panels.isEmpty || model.topPanel.map({ if case .goal = $0.kind { return true }; return false }) == true else { return }
        Task { @MainActor in await model.performUndo() }
    }
}
