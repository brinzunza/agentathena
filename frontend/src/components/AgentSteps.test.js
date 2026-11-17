import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import AgentSteps from './AgentSteps';

const saveStep = (state) => ({
  id: 1, tool: 'save_code', args: { path: '~/Desktop/athenastuff/backtester.py' }, status: state === 'pending' ? 'running' : 'ok',
  permission: {
    id: 'p1', kind: 'save', state,
    code: 'save to /Users/me/Desktop/athenastuff/backtester.py\n30 characters, 2 lines\n\ndef backtest():\n    pass',
  },
});

describe('a request to save a file', () => {
  test('says it wants to save a file, shows where and what, and offers allow / don\'t allow', () => {
    const onDecide = jest.fn();
    render(<AgentSteps steps={[saveStep('pending')]} streaming onDecide={onDecide} />);
    expect(screen.getByText('athena wants to save this file on your computer')).toBeTruthy();
    expect(screen.getByText(/save to \/Users\/me\/Desktop\/athenastuff\/backtester.py/)).toBeTruthy();
    expect(screen.getByText(/def backtest\(\)/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'allow once' }));
    expect(onDecide).toHaveBeenCalledWith(1, 'p1', true);
  });

  test('says "code" nowhere for a file', () => {
    render(<AgentSteps steps={[saveStep('pending')]} streaming onDecide={() => {}} />);
    expect(screen.queryByText(/run this code/)).toBeNull();
  });

  test('the step is labelled as saving a file, with its path', () => {
    render(<AgentSteps steps={[saveStep('approved')]} streaming={false} onDecide={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: /1 step/ }));
    expect(screen.getByText('saved a file')).toBeTruthy();
    expect(screen.getByText('~/Desktop/athenastuff/backtester.py')).toBeTruthy();
    expect(screen.getByText('you allowed this file to be saved')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'allow once' })).toBeNull();      // answered: no buttons
  });

  test('a request for running code still reads as before', () => {
    const run = { id: 2, tool: 'run_python', args: {}, status: 'running', permission: { id: 'p2', kind: 'code', code: 'print(1)', state: 'pending' } };
    render(<AgentSteps steps={[run]} streaming onDecide={() => {}} />);
    expect(screen.getByText('athena wants to run this code on your computer')).toBeTruthy();
  });
});

describe('allowing for the whole chat', () => {
  const pending = (extra = {}) => ({
    id: 3, tool: 'run_python', args: {}, status: 'running',
    permission: { id: 'p3', kind: 'code', code: 'print(1)', state: 'pending', ...extra },
  });

  test('is offered next to allow once, and says which scope it means', () => {
    const onDecide = jest.fn();
    render(<AgentSteps steps={[pending()]} streaming onDecide={onDecide} />);
    fireEvent.click(screen.getByRole('button', { name: 'allow for this chat' }));
    expect(onDecide).toHaveBeenCalledWith(3, 'p3', true, 'chat');
  });

  test('is not offered for risky code, which is asked about every time', () => {
    render(<AgentSteps steps={[pending({ can_remember: false })]} streaming onDecide={() => {}} />);
    expect(screen.queryByRole('button', { name: 'allow for this chat' })).toBeNull();
    expect(screen.getByRole('button', { name: 'allow once' })).toBeTruthy();
  });

  test('not allowing does not carry a scope', () => {
    const onDecide = jest.fn();
    render(<AgentSteps steps={[pending()]} streaming onDecide={onDecide} />);
    fireEvent.click(screen.getByRole('button', { name: "don't allow" }));
    expect(onDecide).toHaveBeenCalledWith(3, 'p3', false);
  });
});

describe('a step that ran without being asked', () => {
  const ran = (approval) => ({ id: 4, tool: 'run_python', args: { code: 'print(1)' }, status: 'ok', result: '1', approval });

  test.each([
    ['chat', 'ran without asking: you allowed this for the chat'],
    ['bypass', 'ran without asking: this chat is in bypass mode'],
  ])('says why (%s)', (approval, words) => {
    render(<AgentSteps steps={[ran(approval)]} streaming={false} onDecide={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: /1 step/ }));
    expect(screen.getByText(words)).toBeTruthy();
  });

  test('a step the user approved one by one has no such line', () => {
    render(<AgentSteps steps={[ran(undefined)]} streaming={false} onDecide={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: /1 step/ }));
    expect(screen.queryByText(/ran without asking/)).toBeNull();
  });
});
