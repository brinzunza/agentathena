import React, { useState } from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import ChatInput from './ChatInput';

// Holds the text the way App does, so typing works
function Harness({ initial = '', onSubmit = jest.fn(), ...props }) {
  const [input, setInput] = useState(initial);
  return (
    <ChatInput
      input={input}
      setInput={setInput}
      onSubmit={(e) => { e.preventDefault(); onSubmit(input); }}
      onCancel={() => {}}
      canChat
      loading={false}
      agentEnabled
      {...props}
    />
  );
}

const box = () => screen.getByRole('textbox', { name: 'message' });

// jsdom does not implement requestSubmit, so route it through a real submit event
beforeAll(() => {
  window.HTMLFormElement.prototype.requestSubmit = function requestSubmit() {
    this.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));
  };
});

describe('the message box', () => {
  test('Enter sends the message', () => {
    const onSubmit = jest.fn();
    render(<Harness initial="hello" onSubmit={onSubmit} />);
    fireEvent.keyDown(box(), { key: 'Enter' });
    expect(onSubmit).toHaveBeenCalledWith('hello');
  });

  test('Shift+Enter makes a new line instead of sending', () => {
    const onSubmit = jest.fn();
    render(<Harness initial="hello" onSubmit={onSubmit} />);
    const notCancelled = fireEvent.keyDown(box(), { key: 'Enter', shiftKey: true });
    expect(onSubmit).not.toHaveBeenCalled();
    expect(notCancelled).toBe(true); // the browser is left to insert the newline
  });

  test('Enter on an empty or blank box sends nothing and adds no line', () => {
    const onSubmit = jest.fn();
    render(<Harness initial="   " onSubmit={onSubmit} />);
    const notCancelled = fireEvent.keyDown(box(), { key: 'Enter' });
    expect(onSubmit).not.toHaveBeenCalled();
    expect(notCancelled).toBe(false);
  });

  test('Enter while an input method is composing is left alone', () => {
    const onSubmit = jest.fn();
    render(<Harness initial="こんにちは" onSubmit={onSubmit} />);
    fireEvent.keyDown(box(), { key: 'Enter', isComposing: true });
    expect(onSubmit).not.toHaveBeenCalled();
  });

  test('the send button is off until there is something to send', () => {
    render(<Harness />);
    expect(screen.getByRole('button', { name: 'Send message' }).disabled).toBe(true);
    fireEvent.change(box(), { target: { value: 'hi' } });
    expect(screen.getByRole('button', { name: 'Send message' }).disabled).toBe(false);
  });

  test('while a reply is arriving the key becomes stop', () => {
    render(<Harness loading />);
    expect(screen.getByRole('button', { name: 'Stop generating' })).toBeTruthy();
    expect(box().disabled).toBe(true);
  });

  test('typing a slash swaps the shortcut hints for the command list', () => {
    render(<Harness />);
    expect(screen.getByText(/to send/)).toBeTruthy();
    fireEvent.change(box(), { target: { value: '/' } });
    expect(screen.getByText('/context')).toBeTruthy();
    expect(screen.queryByText(/to send/)).toBeNull();
  });
});
