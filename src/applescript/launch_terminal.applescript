(*
Author: Hagata
Version: 0.0.1
Date: 2025/1/30 (Created: 2024/10/20)
*)

on run argv
    set base_path to item 1 of argv
    tell application "Terminal"
        activate
        do script "\"" & base_path & "/setup\" --no-terminal"
    end tell
end run
