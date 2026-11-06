
import { useEffect, useState } from 'react'
import './App.css'
import TerminalPanel from './TerminalPanel'

const OPERATION_TIMEOUT_MS = 10 * 60 * 1000

function App() {
  const [vm, setVm] = useState(null)
  const [statusError, setStatusError] = useState('')
  const [commandError, setCommandError] = useState('')
  const [message, setMessage] = useState('')
  const [sending, setSending] = useState(false)
  const [pending, setPending] = useState(null)

  async function refreshStatus() {
    try {
      const response = await fetch('/api/vm/status')

      if (!response.ok) {
        throw new Error('Не удалось получить состояние виртуальной машины')
      }

      const data = await response.json()

      setVm(data)
      setPending((current) => {
        if (!current) {
          return null
        }

        const operationFinished = current.action === 'start'
          ? data.guest_ready
          : !data.running

        return operationFinished ? null : current
      })
      setStatusError('')
    } catch (err) {
      setVm(null)
      setStatusError(err.message)
    }
  }

  useEffect(() => {
    const initialRefresh = setTimeout(refreshStatus, 0)

    const interval = setInterval(refreshStatus, 3000)

    return () => {
      clearTimeout(initialRefresh)
      clearInterval(interval)
    }
  }, [])

  useEffect(() => {
    if (!pending) {
      return
    }

    const remaining = Math.max(
      0,
      OPERATION_TIMEOUT_MS - (Date.now() - pending.startedAt)
    )

    const timeout = setTimeout(() => {
      setPending(null)
      setCommandError(
        'Не удалось подтвердить завершение операции за 10 минут'
      )
    }, remaining)

    return () => clearTimeout(timeout)
  }, [pending])

  async function sendCommand(action) {
    if (sending || pending || vm === null) {
      return
    }

    setSending(true)
    setCommandError('')
    setMessage('')

    if (action === 'start' || action === 'stop') {
      setPending({
        action,
        startedAt: Date.now()
      })
    }

    try {
      const response = await fetch(`/api/vm/${action}`, {
        method: 'POST'
      })

      const result = await response.json()

      if (!response.ok) {
        throw new Error(result.detail || 'Ошибка выполнения команды')
      }

      if (action === 'reboot') {
        setMessage('Команда перезагрузки отправлена. Debian загружается заново.')
      }

      await refreshStatus()
    } catch (err) {
      setPending(null)
      setCommandError(err.message)
    } finally {
      setSending(false)
    }
  }

  const busy = sending || pending !== null

  let statusText = 'Неизвестно'

  if (pending?.action === 'start') {
    statusText = 'Запускается...'
  } else if (pending?.action === 'stop') {
    statusText = 'Выключается...'
  } else if (sending) {
    statusText = 'Выполняется команда...'
  } else if (vm !== null) {
    if (!vm.running) {
      statusText = 'Выключена'
    } else if (!vm.guest_ready) {
      statusText = 'Debian загружается...'
    } else {
      statusText = 'Работает'
    }
  }

  return (
    <main className="container">
      <h1>YADRO VM Manager</h1>
      <p className="description">Управление виртуальной машиной Debian 13</p>

      <section className="panel">
        <h2>Виртуальная машина</h2>

        <p>
          Состояние: <strong>{statusText}</strong>
        </p>

        <div className="buttons">
          <button
            disabled={busy || vm === null || vm.running}
            onClick={() => sendCommand('start')}
          >
            Включить
          </button>

          <button
            disabled={busy || !vm?.guest_ready}
            onClick={() => sendCommand('reboot')}
          >
            Перезагрузить
          </button>

          <button
            disabled={busy || !vm?.guest_ready}
            onClick={() => sendCommand('stop')}
          >
            Выключить
          </button>
        </div>

        {message && <p>{message}</p>}
        {statusError && <p className="error">{statusError}</p>}
        {commandError && <p className="error">{commandError}</p>}
        </section>

        <TerminalPanel
          running={vm?.running === true}
          guestReady={vm?.guest_ready === true}
        />
      </main>
  )
}

export default App
