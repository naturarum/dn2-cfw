// SPDX-License-Identifier: GPL-2.0-or-later
// PatcherApp.swift - Digiwerk Patcher: one window (ContentView), landscape, always dark, its title bar hidden under
// the header bar. A file dropped on the window, chosen with "Choose file", or dropped on the app's icon (Dock or
// Finder) goes to the Model, which runs Core off the main thread.
import AppKit
import SwiftUI

@main
struct PatcherApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var delegate
    static let titleBar = NSWindow.frameRect(forContentRect: NSRect(x: 0, y: 0, width: 100, height: 100),
                                             styleMask: [.titled]).height - 100

    var body: some Scene {
        Window("Digiwerk Patcher", id: "main") {
            // the window is the view's ideal height plus the hidden title bar; the view runs under that bar (the
            // header bar is there), so it asks for less by the bar's height and fills all of ContentView.size
            ContentView(model: Model.shared, ideal: ContentView.size.height - Self.titleBar)
                .ignoresSafeArea()
                .onAppear {
                    for w in NSApp.windows {
                        w.isMovableByWindowBackground = true
                        w.styleMask.remove(.resizable)
                        // the designed size, whatever size an older version saved (only the place is kept)
                        let top = w.frame.maxY
                        w.setFrame(NSRect(x: w.frame.minX, y: top - ContentView.size.height,
                                          width: ContentView.size.width, height: ContentView.size.height),
                                   display: true)
                    }
                }
        }
        .windowStyle(.hiddenTitleBar)
        .windowResizability(.contentSize)
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationWillFinishLaunching(_ notification: Notification) {
        NSApp.appearance = NSAppearance(named: .darkAqua)        // the alerts and panels too, whatever the system's
    }

    func application(_ application: NSApplication, open urls: [URL]) {      // a drop on the app's icon
        if let url = urls.first { Model.shared.process(url) }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

#if DEBUG
    /// debug builds only: DIGIWERK_SNAPSHOT=FILE.png writes the window's geometry (FILE.txt) and its content, drawn
    /// offscreen, a moment after launch (to check the real window's layout without a screen capture)
    func applicationDidFinishLaunching(_ notification: Notification) {
        guard let path = ProcessInfo.processInfo.environment["DIGIWERK_SNAPSHOT"] else { return }
        DispatchQueue.main.asyncAfter(deadline: .now() + 2) {
            guard let w = NSApp.windows.first(where: { $0.isVisible }), let v = w.contentView,
                  let rep = v.bitmapImageRepForCachingDisplay(in: v.bounds) else { return }
            v.cacheDisplay(in: v.bounds, to: rep)
            try? rep.representation(using: .png, properties: [:])?.write(to: URL(fileURLWithPath: path))
            try? ("frame \(w.frame.size) content \(v.frame.size) layout \(w.contentLayoutRect.size) "
                  + "fullSize \(w.styleMask.contains(.fullSizeContentView))\n")
                .write(toFile: path + ".txt", atomically: true, encoding: .utf8)
        }
    }
#endif
}
