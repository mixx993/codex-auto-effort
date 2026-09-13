import AppKit

final class EffortMenu: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private var item: NSStatusItem!
    private var menuIsOpen = false
    private var worker: Process?
    private var pipe: Pipe?
    private var buffer = Data()
    private var snapshot: [String: Any] = [:]
    private var lastUpdate = Date.distantPast
    private var timer: Timer?
    private var chosenThread = UserDefaults.standard.string(forKey: "chosenThread")

    func applicationDidFinishLaunching(_ notification: Notification) {
        if NSRunningApplication.runningApplications(withBundleIdentifier: "io.github.mixx993.codex-auto-effort").count > 1 {
            NSApp.terminate(nil)
            return
        }
        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item.button?.font = NSFont.monospacedSystemFont(ofSize: 12, weight: .medium)
        refresh()
        startWorker()
        timer = Timer.scheduledTimer(withTimeInterval: 2, repeats: true) { [weak self] _ in
            self?.refresh()
        }
    }

    private func startWorker() {
        guard let resources = Bundle.main.resourceURL,
              let data = try? Data(contentsOf: resources.appendingPathComponent("runtime.json")),
              let runtime = try? JSONSerialization.jsonObject(with: data) as? [String: String],
              let python = runtime["python"] else { return }
        let process = Process()
        let output = Pipe()
        process.executableURL = URL(fileURLWithPath: python)
        process.arguments = [resources.appendingPathComponent("monitor.py").path, "--watch"]
        process.standardOutput = output
        process.standardError = FileHandle.nullDevice
        output.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let incoming = handle.availableData
            if incoming.isEmpty {
                handle.readabilityHandler = nil
                return
            }
            DispatchQueue.main.async {
                guard let self = self else { return }
                self.buffer.append(incoming)
                while let newline = self.buffer.firstIndex(of: 10) {
                    let line = self.buffer.prefix(upTo: newline)
                    self.buffer.removeSubrange(...newline)
                    if let value = try? JSONSerialization.jsonObject(with: line) as? [String: Any] {
                        self.snapshot = value
                        self.lastUpdate = Date()
                        self.refresh()
                    }
                }
            }
        }
        do {
            try process.run()
            worker = process
            pipe = output
        } catch {
            output.fileHandleForReading.readabilityHandler = nil
        }
    }

    private func label(_ title: String, _ menu: NSMenu) {
        let entry = NSMenuItem(title: title, action: nil, keyEquivalent: "")
        entry.isEnabled = false
        menu.addItem(entry)
    }

    @discardableResult
    private func action(_ title: String, selector: Selector, menu: NSMenu) -> NSMenuItem {
        let entry = NSMenuItem(title: title, action: selector, keyEquivalent: "")
        entry.target = self
        menu.addItem(entry)
        return entry
    }

    private func timeLabel(_ value: Any?) -> String {
        guard let text = value as? String else { return "未知" }
        let parser = ISO8601DateFormatter()
        parser.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        guard let date = parser.date(from: text) else { return text }
        let formatter = DateFormatter()
        formatter.dateFormat = "MM-dd HH:mm:ss"
        return formatter.string(from: date)
    }

    private func compact(_ text: String, width: CGFloat = 270) -> String {
        let font = NSFont.menuFont(ofSize: 0)
        let clean = text.components(separatedBy: .whitespacesAndNewlines).filter { !$0.isEmpty }.joined(separator: " ")
        if (clean as NSString).size(withAttributes: [.font: font]).width <= width { return clean }
        var result = clean
        while !result.isEmpty && ((result + "…") as NSString).size(withAttributes: [.font: font]).width > width {
            result.removeLast()
        }
        return result + "…"
    }

    private func taskName(_ task: [String: Any]) -> String {
        let name = task["title"] as? String ?? ""
        if name.isEmpty {
            return "任务名称暂不可用 · " + String((task["thread"] as? String ?? "未知").suffix(4))
        }
        return name
    }

    private func submenu(_ title: String, in menu: NSMenu) -> NSMenu {
        let entry = NSMenuItem(title: title, action: nil, keyEquivalent: "")
        let child = NSMenu()
        entry.submenu = child
        menu.addItem(entry)
        return child
    }

    private func header(effort: String, status: String, name: String, mode: String, in menu: NSMenu) {
        let view = NSView(frame: NSRect(x: 0, y: 0, width: 300, height: 116))
        func line(_ text: String, y: CGFloat, size: CGFloat, weight: NSFont.Weight, color: NSColor) {
            let field = NSTextField(labelWithString: text)
            field.font = .systemFont(ofSize: size, weight: weight)
            field.textColor = color
            field.frame = NSRect(x: 16, y: y, width: 268, height: size + 6)
            field.lineBreakMode = .byTruncatingTail
            field.maximumNumberOfLines = 1
            field.toolTip = text
            view.addSubview(field)
        }
        line("CODEX AUTO EFFORT", y: 91, size: 10, weight: .semibold, color: .secondaryLabelColor)
        line(effort + "  ·  " + status, y: 57, size: 20, weight: .semibold, color: .labelColor)
        line(name, y: 31, size: 12, weight: .medium, color: .labelColor)
        line(mode, y: 10, size: 11, weight: .regular, color: .secondaryLabelColor)
        let entry = NSMenuItem()
        entry.view = view
        menu.addItem(entry)
    }

    private func refresh() {
        let tasks = snapshot["tasks"] as? [[String: Any]] ?? []
        let selected = chosenThread == nil ? tasks.first : tasks.first { $0["thread"] as? String == chosenThread }
        let stale = Date().timeIntervalSince(lastUpdate) > 5
        let disabled = snapshot["enabled"] as? Bool == false
        let actual = selected?["actual"] as? String
        let requested = selected?["requested"] as? String ?? "—"
        let phase = selected?["phase"] as? String ?? "selected"
        let effort = actual ?? requested
        let short = ["low": "L", "medium": "M", "high": "H", "xhigh": "XH", "max": "MAX", "ultra": "UL"][effort] ?? compact(effort.uppercased(), width: 55)
        let status = actual != nil ? "已确认" : phase == "rejected" ? "已拒绝" : "待确认"
        let marker = actual != nil ? "✓" : phase == "rejected" ? "!" : "…"
        let name = selected.map(taskName) ?? (chosenThread == nil ? "发送新消息后显示记录" : "所选任务暂无近期记录")
        let mode = chosenThread == nil ? "跟随最近请求 · 不跟随前台窗口" : "固定查看此任务"
        item.button?.title = stale ? "?" : disabled ? "OFF" : selected == nil ? "—" : "\(short) \(marker)"
        item.button?.toolTip = "Codex Auto Effort\n\(effort.capitalized) · \(status)\n\(name)"
        item.button?.setAccessibilityLabel("Codex Auto Effort")
        item.button?.setAccessibilityValue(stale ? "监测未连接" : disabled ? "自动选档关闭" : "\(effort) · \(status)")
        // Keep the open menu stable while the background feed updates.
        guard !menuIsOpen else { return }
        let menu = NSMenu()
        menu.delegate = self
        header(effort: stale ? "未连接" : disabled ? "已关闭" : selected == nil ? "等待记录" : effort.capitalized,
               status: stale ? "监测暂停" : disabled ? "自动选档" : selected == nil ? "就绪" : status,
               name: name, mode: mode, in: menu)
        menu.addItem(.separator())
        if let task = selected {
            let details = submenu("详情", in: menu)
            if stale || disabled { label("以下为历史记录", details) }
            label("实际档位：\(actual ?? "待确认")", details)
            label("记录时间：\(timeLabel(task["actual_time"]))", details)
            label(compact("来源：\(task["source"] as? String ?? "本地请求日志")"), details)
            label(compact(task["association"] as? String ?? "等待本轮服务端记录"), details)
            details.addItem(.separator())
            label("自动选择：\(requested) · \(timeLabel(task["time"]))", details)
            label("原档位：\(task["original"] as? String ?? "未记录")", details)
            label(compact("模型：\(task["model"] as? String ?? "待确认")"), details)
            label(compact("原因：\(task["reason"] as? String ?? "未知")"), details)
        }
        let chooser = submenu("查看任务", in: menu)
        let automatic = action("跟随最近请求", selector: #selector(selectTask(_:)), menu: chooser)
        automatic.state = chosenThread == nil ? .on : .off
        if !tasks.isEmpty { chooser.addItem(.separator()) }
        for task in tasks {
            let fullName = taskName(task)
            let entry = action(compact(fullName), selector: #selector(selectTask(_:)), menu: chooser)
            entry.toolTip = fullName
            entry.representedObject = task["thread"]
            entry.state = task["thread"] as? String == chosenThread ? .on : .off
        }
        let more = submenu("更多", in: menu)
        action("重新连接", selector: #selector(reconnect), menu: more)
        action("项目说明", selector: #selector(openProject), menu: more)
        more.addItem(.separator())
        let quitItem = action("退出菜单栏", selector: #selector(quit), menu: more)
        quitItem.toolTip = "后台自动选档继续工作"
        item.menu = menu
    }

    func menuWillOpen(_ menu: NSMenu) { menuIsOpen = true }

    func menuDidClose(_ menu: NSMenu) {
        menuIsOpen = false
        DispatchQueue.main.async { [weak self] in self?.refresh() }
    }

    @objc private func selectTask(_ sender: NSMenuItem) {
        chosenThread = sender.representedObject as? String
        UserDefaults.standard.set(chosenThread, forKey: "chosenThread")
        refresh()
    }

    private func stopWorker() {
        pipe?.fileHandleForReading.readabilityHandler = nil
        if worker?.isRunning == true { worker?.terminate() }
        worker = nil
        pipe = nil
        buffer.removeAll()
    }

    @objc private func reconnect() {
        stopWorker()
        lastUpdate = .distantPast
        startWorker()
        refresh()
    }

    @objc private func openProject() {
        NSWorkspace.shared.open(URL(string: "https://github.com/mixx993/codex-auto-effort")!)
    }

    @objc private func quit() { NSApp.terminate(nil) }

    func applicationWillTerminate(_ notification: Notification) {
        timer?.invalidate()
        stopWorker()
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let delegate = EffortMenu()
app.delegate = delegate
app.run()
