$ErrorActionPreference = 'Stop'
$appId = $env:TODO_TOAST_APPID
$title = $env:TODO_TOAST_TITLE
$msg   = $env:TODO_TOAST_MSG
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null
$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$texts = $template.GetElementsByTagName('text')
$texts.Item(0).AppendChild($template.CreateTextNode($title)) | Out-Null
$texts.Item(1).AppendChild($template.CreateTextNode($msg)) | Out-Null
$toast = [Windows.UI.Notifications.ToastNotification]::new($template)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show($toast)
Write-Output 'TOAST_SENT'
