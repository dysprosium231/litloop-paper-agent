!macro NSIS_HOOK_POSTUNINSTALL
  ${If} $UpdateMode != 1
    ${If} ${FileExists} "$PROFILE\.litloop\*.*"
      MessageBox MB_YESNO|MB_ICONQUESTION|MB_DEFBUTTON2 \
        "是否同时删除 LitLoop 用户数据目录（$PROFILE\.litloop）？$\r$\n$\r$\n其中包含检索记录、已下载的 PDF 和配置文件。选择「否」将保留这些数据。$\r$\n$\r$\nRemove LitLoop user data directory ($PROFILE\.litloop)?$\r$\nIt stores screening records, downloaded PDFs, and configuration. Choose No to keep your data." \
        /SD IDNO IDNO _litloop_keep_data
      RMDir /r "$PROFILE\.litloop"
      _litloop_keep_data:
    ${EndIf}
  ${EndIf}
!macroend
