# 如何手动启动 Historical Quote Inquiry 系统

如果您不想使用提供的 `start_server.ps1` 一键脚本，也可以完全通过终端手动启动项目。

> **您之前报错的原因分析：**
> 1. 您在终端里执行了 `cd .\venv\Scripts\` 进入了很深的目录，然后又执行了 `venv\Scripts\Activate.ps` (而且漏了扩展名 `1`)。由于您当前目录已经变了，相对路径找不到文件，自然就报错了。
> 2. 同样的道理，在执行 `.\venv\Scripts\python.exe` 时，因为您所在的目录已经是 `E:\hl_Historical_Quote_Inquiry\venv\Scripts`，再往下找 `.\venv\...` 肯定是不存在的。

要成功手动启动，**请始终保持在项目的根目录（即 `E:\hl_Historical_Quote_Inquiry`）下操作。**

---

## 方式一：在 PowerShell 中手动启动 (推荐)

打开您的终端 (确保提示符路径是 `E:\hl_Historical_Quote_Inquiry`)，依次执行以下两条命令：

**1. 激活虚拟环境：**
```powershell
.\venv\Scripts\Activate.ps1
```
*(成功后，您的终端提示符最左侧会出现 `(venv)` 的字样)*

**2. 启动 FastAPI 服务：**
```powershell
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

---

## 方式二：在 CMD (命令提示符) 中手动启动

如果您使用的是老式的 CMD 终端，命令稍微有一点区别：

**1. 激活虚拟环境：**
```cmd
.\venv\Scripts\activate.bat
```

**2. 启动 FastAPI 服务：**
```cmd
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

---

## 方式三：不激活虚拟环境，直接硬调用

如果您就是不想激活虚拟环境，只要您确保当前路径是在项目根目录下，您可以直接指定虚拟环境里 python 解释器的绝对/相对路径来运行：

```powershell
.\venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

---

## 常见问题
如果在启动时提示 `[WinError 10048] 通常每个套接字地址(协议/网络地址/端口)只允许使用一次`，说明 8000 端口被之前的进程卡死了。
在 PowerShell 中执行以下命令强杀即可：
```powershell
Stop-Process -Id (Get-NetTCPConnection -LocalPort 8000).OwningProcess -Force
```
